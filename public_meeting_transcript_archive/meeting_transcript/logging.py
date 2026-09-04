import functools
import logging
logger = logging.getLogger(__name__)


def log(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        message = f"calling {func.__name__}"
        logger.warning(message.upper())

        return func(*args, **kwargs)
    
    return wrapper