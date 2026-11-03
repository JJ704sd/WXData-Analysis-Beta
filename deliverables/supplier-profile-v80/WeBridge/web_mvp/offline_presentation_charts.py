"""Extract cached chart values from OOXML presentations.

Chart caches are useful evidence when a slide has no text, but they are not a
recalculation of the workbook behind a chart.  This module deliberately reads
only the chart XML supplied by the presentation relationship.
"""

C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"

_SUPPORTED = {
    "barChart": "bar",
    "lineChart": "line",
    "pieChart": "pie",
    "scatterChart": "scatter",
}


def _cache(cache):
    """Return sparse cache points and the cache's optional number format."""
    values = {}
    format_code = cache.findtext(C + "formatCode", "")
    for point in cache.findall(C + "pt"):
        raw_index = point.get("idx")
        if raw_index is None:
            continue
        try:
            index = int(raw_index)
        except ValueError:
            continue
        value = point.findtext(C + "v")
        if value is not None:
            values[index] = value
    return values, format_code


def _reference(node, references):
    if node is None:
        return {}, ""
    for reference_tag, cache_tag in references:
        reference = node.find(C + reference_tag)
        if reference is None:
            continue
        cache = reference if cache_tag is None else reference.find(C + cache_tag)
        if cache is not None:
            return _cache(cache)
    return {}, ""


def _cached_text(node):
    if node is None:
        return ""
    value = node.findtext(C + "v")
    if value:
        return value
    for reference in node.findall(C + "strRef"):
        cache = reference.find(C + "strCache")
        if cache is None:
            continue
        values, _ = _cache(cache)
        if values:
            return values[min(values)]
    rich = node.find(C + "rich")
    if rich is not None:
        drawing_text = "{http://schemas.openxmlformats.org/drawingml/2006/main}t"
        return "".join(text.text or "" for text in rich.iter(drawing_text))
    return ""


def _series_name(series):
    tx = series.find(C + "tx")
    if tx is None:
        return "未命名系列"
    return _cached_text(tx) or "未命名系列"


def _chart_title(root):
    chart = root.find(C + "chart")
    title = chart.find(C + "title") if chart is not None else None
    if title is None:
        return "未提供"
    tx = title.find(C + "tx")
    if tx is None:
        return "未提供"
    return _cached_text(tx) or "未提供"


def _ordinary_points(series):
    categories, _ = _reference(
        series.find(C + "cat"),
        (("strRef", "strCache"), ("numRef", "numCache"),
         ("strLit", None), ("numLit", None)),
    )
    values, format_code = _reference(
        series.find(C + "val"),
        (("numRef", "numCache"), ("numLit", None)),
    )
    return categories, values, format_code


def _xy_points(series):
    x_values, x_format = _reference(
        series.find(C + "xVal"),
        (("numRef", "numCache"), ("numLit", None)),
    )
    y_values, y_format = _reference(
        series.find(C + "yVal"),
        (("numRef", "numCache"), ("numLit", None)),
    )
    return x_values, y_values, x_format, y_format


def extract_chart(root, out, label):
    """Append supported chart cache data to a ``TextResult``-like object.

    The caller resolves and parses the relationship target before calling this
    function.  The function returns the number of extracted series.  It
    intentionally does not follow formulas, external workbook links, or
    arbitrary XML strings.
    """
    title = _chart_title(root)
    series_count = 0
    missing_cache = False
    for chart_node in root.iter():
        chart_type = _SUPPORTED.get(chart_node.tag.rsplit("}", 1)[-1])
        if chart_type is None:
            continue
        for series in chart_node.findall(C + "ser"):
            series_name = _series_name(series)
            lines = [
                "图表标题：" + title,
                "图表类型：" + chart_type,
                "系列：" + series_name,
            ]
            if chart_type == "scatter":
                x_values, y_values, x_format, y_format = _xy_points(series)
                indices = sorted(set(x_values) | set(y_values))
                if indices:
                    lines.append("数据（缓存未重算）：")
                    for index in indices:
                        x_value = x_values.get(index, "（缺失）")
                        y_value = y_values.get(index, "（缺失）")
                        lines.append(f"idx={index} x={x_value} y={y_value}")
                else:
                    lines.append("数据缓存缺失：未提取公式或外部工作簿。")
                    missing_cache = True
                if y_format:
                    lines.insert(3, "数值格式：" + y_format)
                if x_format:
                    lines.insert(3, "x数值格式：" + x_format)
            else:
                categories, values, format_code = _ordinary_points(series)
                indices = sorted(set(categories) | set(values))
                if indices:
                    lines.append("数据（缓存未重算）：")
                    for index in indices:
                        category = categories.get(index, "（缺失）")
                        value = values.get(index, "（缺失）")
                        lines.append(f"idx={index} 类别：{category} 值：{value}")
                else:
                    lines.append("数据缓存缺失：未提取公式或外部工作簿。")
                    missing_cache = True
            if chart_type != "scatter" and format_code:
                lines.insert(3, "数值格式：" + format_code)
            out.add(f"{label} · 图表 · 系列 {series_name}", "\n".join(lines))
            series_count += 1
    if series_count:
        notice = f"{label}：图表数据来自缓存未重算；未执行公式或外部工作簿读取。"
        if missing_cache:
            notice += "部分系列缺少图表缓存，未猜测其数值。"
        else:
            notice += "与嵌入工作簿实时值可能不一致。"
        out.notices.append(notice)
    else:
        out.notices.append(f"{label}：图表类型未支持，未猜测图表数据。")
    return series_count
