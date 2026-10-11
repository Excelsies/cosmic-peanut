import os

# Same as tidalviz.app: a single-threaded OpenBLAS pool, set before any tool imports numpy.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
