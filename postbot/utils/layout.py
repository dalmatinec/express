"""Расстановка кнопок по рядам, как в satpaev. Одно нажатие стрелки - один шаг.

⬅️ ➡️ меняют кнопку местами с соседом в ряду.
⬆️ если в ряду выше меньше трёх кнопок, кнопка встаёт туда последней.
   Если там уже три, а в её ряду есть ещё кнопки, она уходит отдельным рядом над своим.
   Если она и так одна в ряду, она меняется местами с рядом выше.
⬇️ то же самое вниз, в ряду ниже она встаёт первой.
↕️ Отдельно - вынести кнопку из ряда в свой ряд ниже.
"""
from typing import Iterable

ROW_MAX = 3  # стрелками в один ряд собирается не больше трёх кнопок

Grid = list[list[int]]


def grid_of(items: Iterable) -> Grid:
    """Ряды из кнопок, уже отсортированных по (row, position)."""
    grid: Grid = []
    current = None
    for item in items:
        if item.row != current or not grid:
            grid.append([])
            current = item.row
        grid[-1].append(item.id)
    return grid


def _find(grid: Grid, item_id: int) -> tuple[int, int]:
    for r, row in enumerate(grid):
        if item_id in row:
            return r, row.index(item_id)
    return -1, -1


def move(grid: Grid, item_id: int, direction: str) -> Grid:
    grid = [list(r) for r in grid if r]
    r, c = _find(grid, item_id)
    if r < 0:
        return grid
    row = grid[r]
    if direction == "left" and c > 0:
        row[c - 1], row[c] = row[c], row[c - 1]
    elif direction == "right" and c < len(row) - 1:
        row[c + 1], row[c] = row[c], row[c + 1]
    elif direction == "up":
        if r == 0 and len(row) == 1:
            return grid  # и так в самом верху
        row.pop(c)
        alone = not row
        if r > 0 and len(grid[r - 1]) < ROW_MAX:
            grid[r - 1].append(item_id)
        elif not alone:
            grid.insert(r, [item_id])
        else:
            grid.insert(r - 1, [item_id])
    elif direction == "down":
        if r == len(grid) - 1 and len(row) == 1:
            return grid  # и так в самом низу
        row.pop(c)
        alone = not row
        if r + 1 < len(grid) and len(grid[r + 1]) < ROW_MAX:
            grid[r + 1].insert(0, item_id)
        elif not alone:
            grid.insert(r + 1, [item_id])
        else:
            grid.insert(r + 2, [item_id])
    elif direction == "solo" and len(row) > 1:
        row.pop(c)
        grid.insert(r + 1, [item_id])
    return [x for x in grid if x]


def rows_of(grid: Grid) -> list[tuple[int, int, int]]:
    """-> (row, position, id) для записи в базу."""
    return [(r, p, item_id) for r, row in enumerate(grid) for p, item_id in enumerate(row)]
