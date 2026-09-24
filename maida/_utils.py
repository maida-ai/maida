import sys
import warnings
import functools
from types import MethodType
import inspect

__all__ = [
    "deprecated",
]


_PY_VERSION = (sys.version_info.major, sys.version_info.minor, sys.version_info.micro)


# @warnings.deprecated is introduced in Python 3.13
if _PY_VERSION < (3, 13):

    class deprecated:
        """Indicate that a class, function or overload is deprecated.
        From https://github.com/python/cpython/blob/a2864ec64352832bd7eb2afed4e0164d1368ceef/Lib/warnings.py#L518
        """

        def __init__(
            self,
            message: str,
            /,
            *,
            category: type[Warning] | None = DeprecationWarning,
            stacklevel: int = 1,
        ) -> None:
            if not isinstance(message, str):
                raise TypeError(f"Expected an object of type str for 'message', not {type(message).__name__!r}")
            self.message = message
            self.category = category
            self.stacklevel = stacklevel

        def __call__(self, arg, /):
            msg = self.message
            category = self.category
            stacklevel = self.stacklevel
            if category is None:
                arg.__deprecated__ = msg
                return arg
            elif isinstance(arg, type):
                original_new = arg.__new__

                @functools.wraps(original_new)
                def __new__(cls, /, *args, **kwargs):
                    if cls is arg:
                        warnings.warn(msg, category=category, stacklevel=stacklevel + 1)
                    if original_new is not object.__new__:
                        return original_new(cls, *args, **kwargs)
                    # Mirrors a similar check in object.__new__.
                    elif cls.__init__ is object.__init__ and (args or kwargs):
                        raise TypeError(f"{cls.__name__}() takes no arguments")
                    else:
                        return original_new(cls)

                arg.__new__ = staticmethod(__new__)

                if "__init_subclass__" in arg.__dict__:
                    original_init_subclass = arg.__init_subclass__
                    if isinstance(original_init_subclass, MethodType):
                        original_init_subclass = original_init_subclass.__func__

                    @functools.wraps(original_init_subclass)
                    def __init_subclass__(*args, **kwargs):
                        warnings.warn(msg, category=category, stacklevel=stacklevel + 1)
                        return original_init_subclass(*args, **kwargs)
                else:

                    def __init_subclass__(cls, *args, **kwargs):
                        warnings.warn(msg, category=category, stacklevel=stacklevel + 1)
                        return super(arg, cls).__init_subclass__(*args, **kwargs)

                arg.__init_subclass__ = classmethod(__init_subclass__)

                arg.__deprecated__ = __new__.__deprecated__ = msg
                __init_subclass__.__deprecated__ = msg
                return arg
            elif callable(arg):

                @functools.wraps(arg)
                def wrapper(*args, **kwargs):
                    warnings.warn(msg, category=category, stacklevel=stacklevel + 1)
                    return arg(*args, **kwargs)

                if inspect.iscoroutinefunction(arg):
                    wrapper = inspect.markcoroutinefunction(wrapper)

                arg.__deprecated__ = wrapper.__deprecated__ = msg
                return wrapper
            else:
                raise TypeError(
                    f"@deprecated decorator with non-None category must be applied to a class or callable, not {arg!r}"
                )
else:
    from warnings import deprecated as _deprecated

    deprecated = _deprecated
