"""Hit areas in the final Agg layout, in the PNG's own pixel coordinates."""
from math import isfinite

from matplotlib.patches import Patch


class ChartMarks:
    def __init__(self):
        self.groups = []
        self.items = []

    def group(self, label, detail):
        index = len(self.groups)
        self.groups.append({"label": label, "detail": detail})
        return index

    def artist(self, group, artist):
        self.items.append((group, artist, None))

    def point(self, group, ax, x, y):
        self.items.append((group, ax, (ax.convert_xunits(x), y)))

    def export(self, canvas, title):
        width, height = canvas.get_width_height()
        renderer = canvas.get_renderer()
        shapes = []
        for group, artist, point in self.items:
            if point is not None:
                x, y = artist.transData.transform(point)
                shape = {"circle": [round(x, 1), round(height - y, 1), 6]}
            elif isinstance(artist, Patch):
                polygons = artist.get_path().transformed(artist.get_transform()).to_polygons()
                paths = []
                for polygon in polygons:
                    if len(polygon) and all(isfinite(v) for xy in polygon for v in xy):
                        paths.append("M" + "L".join(f"{x:.1f} {height-y:.1f}" for x, y in polygon) + "Z")
                if not paths:
                    continue
                shape = {"path": "".join(paths)}
            else:
                box = artist.get_window_extent(renderer).padded(2)
                shape = {"rect": [round(box.x0, 1), round(height-box.y1, 1),
                                  round(box.width, 1), round(box.height, 1)]}
            shapes.append({"group": group, **shape})
        return {"width": width, "height": height, "title": title, "groups": self.groups, "shapes": shapes}
