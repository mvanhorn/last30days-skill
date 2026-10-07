Reddit RSS feed parsing now rejects DOCTYPE/ENTITY payloads (XML entity expansion) using `defusedxml` when available, with a stdlib DTD guard otherwise.
