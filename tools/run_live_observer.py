"""Live vision and UI observation only; no Android input flags or backend."""
from benchmark_live_capture import main

if __name__=='__main__': main(observer=True)
