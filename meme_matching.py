from __future__ import annotations

from database import Meme


def find_best_match(memes: list[Meme], content: str) -> Meme | None:
    stripped_content = content.strip()
    matches: list[Meme] = []

    for meme in memes:
        keyword = meme.keyword.strip()
        if not keyword:
            continue

        if meme.match_type == "exact" and stripped_content == keyword:
            matches.append(meme)
        elif meme.match_type == "partial" and keyword in content:
            matches.append(meme)

    if not matches:
        return None

    return sorted(matches, key=lambda meme: (-len(meme.keyword), meme.id))[0]
