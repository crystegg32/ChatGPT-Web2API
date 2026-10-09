"""Non-throwing input size metadata; never transforms or retains input."""


def utf8_size(text: str) -> int | None:
    try:
        return len(text.encode("utf-8"))
    except UnicodeEncodeError:
        return None
