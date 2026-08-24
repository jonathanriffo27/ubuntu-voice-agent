import ctypes
import os


def mute_alsa_logging():
    """
    Silencia los mensajes de error C de bajo nivel de ALSA
    (como 'underrun occurred', 'pcm.c: snd_pcm_recover', etc.)
    que contaminan la consola en Linux.
    """
    if os.name != 'posix':
        return

    try:
        ERROR_HANDLER_FUNC = ctypes.CFUNCTYPE(
            None,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p
        )

        def py_error_handler(filename, line, function, err, fmt):
            pass

        # Guardar referencia para evitar que el garbage collector lo limpie
        global _c_error_handler
        _c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)

        asound = ctypes.cdll.LoadLibrary('libasound.so.2')
        asound.snd_lib_error_set_handler(_c_error_handler)
    except Exception:
        pass
