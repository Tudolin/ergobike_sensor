# PyInstaller entry point (the package itself can't be the script: relative imports)
import sys

from ergobike.__main__ import main

sys.exit(main())
