"""
Decode a secret message from a published Google Doc containing Unicode characters
and their positions in a 2D grid.

Usage:
    python decode_secret_message.py
"""

import re
from html import unescape

import requests


def decode_secret_message(url: str) -> None:
    """
    Fetches a published Google Doc from the given URL, parses the character
    grid data, and prints the resulting graphic to stdout.

    The Google Doc is expected to contain a table with three columns:
        x-coordinate | Character | y-coordinate

    Parameters
    ----------
    url : str
        The published Google Doc URL containing the input data.
    """
    # 1. Fetch the document
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    html = resp.text

    # 2. Parse the table rows with a regex that captures three <td> cells
    #    The Google Docs published HTML wraps each cell value inside
    #    <td ...><p ...><span ...>VALUE</span></p></td>.
    #    We extract all span text triples, then filter to those where the
    #    first and third values are ints (ignoring the header row).
    pattern = re.compile(
        r"<td[^>]*>.*?<span[^>]*>(.*?)</span>.*?</td>\s*"
        r"<td[^>]*>.*?<span[^>]*>(.*?)</span>.*?</td>\s*"
        r"<td[^>]*>.*?<span[^>]*>(.*?)</span>.*?</td>",
        re.DOTALL,
    )

    points: list[tuple[int, int, str]] = []
    for match in pattern.finditer(html):
        x_str = unescape(match.group(1).strip())
        char = unescape(match.group(2).strip())
        y_str = unescape(match.group(3).strip())

        # Skip the header row and any non-data rows
        try:
            x = int(x_str)
            y = int(y_str)
        except ValueError:
            continue

        # Skip empty characters
        if not char:
            continue

        points.append((x, y, char))

    if not points:
        print("No grid data found in the document.")
        return

    # 3. Determine grid dimensions
    max_x = max(p[0] for p in points)
    max_y = max(p[1] for p in points)

    # 4. Build the grid (y rows, x columns), filled with spaces
    grid = [[" " for _ in range(max_x + 1)] for _ in range(max_y + 1)]

    for x, y, char in points:
        grid[y][x] = char

    # 5. Print the grid
    for row in grid:
        print("".join(row))


if __name__ == "__main__":
    # Example URL from the problem statement (draws an 'F')
    example_url = (
        "https://docs.google.com/document/d/e/"
        "2PACX-1vTMOmshQe8YvaRXi6gEPKKlsC6UpFJSMAk4mQjLm_u1gmHdVVTaeh7nBNFBRlui0sTZ-snGwZM4DBCT/pub"
    )
    decode_secret_message(example_url)
