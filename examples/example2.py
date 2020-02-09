import os
import sys
import logging

sys.path.append(os.getcwd() + '/..')

import trio
from async_generator import aclosing

from bitmex_trio_websocket import BitMEXWebsocket

logging.basicConfig(level='INFO', format='[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s')

async def main():
    async with BitMEXWebsocket.connect('testnet') as bws:
        count = 0
        async with aclosing(bws.listen('tradeBin1h', 'XBTM20')) as agen:
            async for msg in agen:
                print(f'Received message, symbol: \'{msg["symbol"]}\', timestamp: \'{msg["timestamp"]}\'')
                count += 1
                if count == 5:
                    break


if __name__ == '__main__':
    trio.run(main)