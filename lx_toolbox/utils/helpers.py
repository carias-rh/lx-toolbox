import time

_counter = 1

def step_logger(step_str: str, patience: int = 1):
    """
    Prints a formatted step message and increments a global counter.
    """
    global _counter
    print(f"Step {_counter}: {step_str}")
    print('-' * 40)
    _counter += 1
    if patience > 0:
        time.sleep(patience)

def reset_step_counter(start_value: int = 1):
    """Resets the global step counter."""
    global _counter
    _counter = start_value 