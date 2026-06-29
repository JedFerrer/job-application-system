from html.parser import HTMLParser
from urllib.request import Request, urlopen


class GoogleDocTableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []

        self._in_table = False
        self._in_row = False
        self._in_cell = False

        self._current_table = []
        self._current_row = []
        self._current_cell = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()

        if tag == "table":
            self._in_table = True
            self._current_table = []

        elif self._in_table and tag == "tr":
            self._in_row = True
            self._current_row = []

        elif self._in_table and self._in_row and tag in ("td", "th"):
            self._in_cell = True
            self._current_cell = []

    def handle_data(self, data):
        if self._in_cell:
            self._current_cell.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag in ("td", "th") and self._in_cell:
            cell_text = "".join(self._current_cell).strip()
            self._current_row.append(cell_text)
            self._in_cell = False
            self._current_cell = []

        elif tag == "tr" and self._in_row:
            if self._current_row:
                self._current_table.append(self._current_row)
            self._in_row = False
            self._current_row = []

        elif tag == "table" and self._in_table:
            self.tables.append(self._current_table)
            self._in_table = False
            self._current_table = []


def print_google_doc_grid(url: str) -> None:
    """
    Fetches a published Google Doc containing rows of:
    x-coordinate | Character | y-coordinate

    Then prints the corresponding 2D character grid.
    """

    request = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"}
    )

    with urlopen(request) as response:
        html = response.read().decode("utf-8")

    parser = GoogleDocTableParser()
    parser.feed(html)

    points = {}

    for table in parser.tables:
        for row in table:
            if len(row) < 3:
                continue

            try:
                x = int(row[0])
                char = row[1]
                y = int(row[2])
            except ValueError:
                # Skip the header row or any non-data row.
                continue

            points[(x, y)] = char

    if not points:
        raise ValueError("No valid grid data found in the document.")

    max_x = max(x for x, _ in points)
    max_y = max(y for _, y in points)

    for y in range(max_y + 1):
        line = []

        for x in range(max_x + 1):
            line.append(points.get((x, y), " "))

        print("".join(line))


# Example usage:
print_google_doc_grid(
    "https://docs.google.com/document/d/e/2PACX-1vSvM5gDlNvt7npYHhp_XfsJvuntUhq184By5xO_pA4b_gCWeXb6dM6ZxwN8rE6S4ghUsCj2VKR21oEP/pub"
)
