# -*- coding: utf-8 -*-

"""BitMEX Websocket Connection."""
from collections import Counter
import math
import logging
from typing import Optional, Sequence

from async_generator import aclosing, asynccontextmanager
import trio
from slurry import Pipeline
from slurry.sections import Merge, Repeat
from slurry_websocket import Websocket, ConnectionClosed

from .auth import generate_expires, generate_signature
from .storage import MemoryStorage
from .logger import Logger
from .parser import Parser

log = logging.getLogger(__name__)

class BitMEXWebsocket:
    def __init__(self, storage=None):
        if storage:
            self.storage = storage
        else:
            self.storage = MemoryStorage()
        self._pipeline = None
        self._send_channel = None
        self._subscriptions = Counter()
        self._connectionclosed = None
    
    async def listen(self, table: str, *symbols: Optional[Sequence[str]]):
        """
        Subscribe to a channel and optionally one or more specific symbols.
        
        Returns an async generator that yields messages from the subscribed channel.
        """
        if self._connectionclosed is not None:
            raise trio.ClosedResourceError('Connection is closed.')

        listeners = [(table,)] if not symbols else [(table, symbol) for symbol in symbols]

        args = []
        for listener in listeners:
            if self._subscriptions[listener] == 0:
                args.append(listener[0] if not symbols else ':'.join(listener))
            self._subscriptions[listener] += 1
        await self._send_channel.send({'op': 'subscribe', 'args': args})

        async with self._pipeline.tap() as aiter:
            async for item, item_symbol, item_table, _ in aiter:
                # Lock list of listeners while sending
                if item_table == table and (not symbols or item_symbol in symbols):
                    yield item

        log.debug('Listener detached from table: %s, symbol: %s', table, symbols)
        args = []
        for listener in listeners:
            self._subscriptions[listener] -= 1
            if self._subscriptions[listener] == 0:
                log.debug('No more listeners on table: %s, symbol: %s. Unsubscribing.', table, symbols)
                args.append(listener[0] if not symbols else ':'.join(listener))
        if args:
            await self._send_channel.send({'op': 'unsubscribe', 'args': args})

    @asynccontextmanager
    async def _connect(self, network, api_key, api_secret, dead_mans_switch):
        """Open a BitMEX websocket connection."""
        try:
            if network == 'mainnet':
                url = 'wss://www.bitmex.com/realtime'
            else:
                url = 'wss://testnet.bitmex.com/realtime'

            log.debug('Generating authentication headers.')
            # To auth to the WS using an API key, we generate a signature of a nonce and
            # the WS API endpoint.
            headers = None
            if api_key and api_secret:
                nonce = generate_expires()
                headers = [
                    ('api-expires', str(nonce)),
                    ('api-signature', generate_signature(api_secret, 'GET', '/realtime', nonce, '')),
                    ('api-key', api_key)
                ]

            send_channel, receive_channel = trio.open_memory_channel(math.inf)
            self._send_channel = send_channel

            if dead_mans_switch:
                sections = [Merge(receive_channel, Repeat(15, default={'op': 'cancelAllAfter', 'args': 60000}))]
            else:
                sections = [receive_channel]

            if log.root.level <= logging.DEBUG:
                sections.append(Logger())

            sections.append(Websocket(url, extra_headers=headers))
            sections.append(Parser())
            sections.append(self.storage)

            async with Pipeline.create(*sections) as pipeline:
                self._pipeline = pipeline
                log.debug('Opening websocket connection.')
                yield self
                log.debug('BitMEXWebsocket context exit. Cancelling running tasks.')
                pipeline.nursery.cancel_scope.cancel()
            log.debug('BitMEXWebsocket closed.')

        except OSError as ose:
            log.error('Connection attempt failed: %s', type(ose).__name__)
        except trio.BrokenResourceError as re:
            print(re)

@asynccontextmanager
async def open_bitmex_websocket(network: str, api_key: str=None, api_secret: str=None, *, dead_mans_switch=False, storage=None):
    """Open a new BitMEX websocket connection context."""
    if network not in ('mainnet', 'testnet'):
        raise ValueError('network argument must be either \'mainnet\' or \'testnet\'')
    
    bitmex_websocket = BitMEXWebsocket(storage)
    #pylint: disable=not-async-context-manager
    async with bitmex_websocket._connect(network, api_key, api_secret, dead_mans_switch): 
        yield bitmex_websocket
