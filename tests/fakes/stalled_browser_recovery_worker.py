"""A real, inert child with a command pipe that never acknowledges shutdown."""

import sys
import time

if __name__ == "__main__":
    print("READY", flush=True)
    sys.stdin.readline()
    time.sleep(60)
