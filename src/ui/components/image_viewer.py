"""Streamlit tools for inspecting and comparing grayscale images."""

from base64 import b64encode
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from math import ceil, floor

import altair as alt
import numpy as np
import streamlit as st
from PIL import Image, ImageDraw

from src.models import ROI, ROIStatistics


@dataclass
class ViewSettings:
    zoom: int = 100
    pan_x: int = 0
    pan_y: int = 0
    brightness: int = 100
    contrast: int = 100


def _viewport(image: np.ndarray, roi: ROI | None, settings: ViewSettings, size: int) -> Image.Image:
    """Render a square viewport without modifying the underlying pixels."""
    rows, columns = image.shape
    ratio = size / max(rows, columns) * settings.zoom / 100
    width = max(1, round(columns * ratio))
    height = max(1, round(rows * ratio))
    left = (size - width) // 2 + round(settings.pan_x * max(0, width - size) / 200)
    top = (size - height) // 2 + round(settings.pan_y * max(0, height - size) / 200)

    raw = Image.fromarray(image)
    if settings.brightness != 100 or settings.contrast != 100:
        lut = [
            max(0, min(255, round(((value - 127.5) * settings.contrast / 100 + 127.5)
                                 * settings.brightness / 100)))
            for value in range(256)
        ]
        raw = raw.point(lut)
    frame = Image.new("RGB", (size, size), (7, 7, 7))
    frame.paste(raw.resize((width, height), Image.Resampling.BILINEAR), (left, top))
    if roi is not None:
        x0 = left + round(roi["x"] * width / columns)
        x1 = left + round((roi["x"] + roi["width"]) * width / columns) - 1
        y0 = top + round(roi["y"] * height / rows)
        y1 = top + round((roi["y"] + roi["height"]) * height / rows) - 1
        if x1 >= x0 and y1 >= y0:
            ImageDraw.Draw(frame).rectangle((x0, y0, x1, y1), outline="#61d4bf", width=2)
    return frame


def _change_zoom(key: str, difference: int) -> None:
    state: ViewSettings = st.session_state[key]
    state.zoom = min(225, max(50, state.zoom + difference))
    if state.zoom <= 100:
        for axis in ("pan_x", "pan_y"):
            setattr(state, axis, 0)
            st.session_state[f"{key}-{axis}"] = 0


def _reset_view(key: str) -> None:
    st.session_state[key] = ViewSettings()
    for name, value in vars(st.session_state[key]).items():
        if name != "zoom":
            st.session_state[f"{key}-{name}"] = value


def render_images(
    original: np.ndarray,
    result: np.ndarray | None,
    result_label: str,
    *,
    key: str,
    active_image: str,
    expanded: bool,
    roi: ROI | None = None,
    roi_enabled: bool = False,
    selecting_roi: bool = False,
    on_toggle_roi: Callable[[], None] | None = None,
    roi_revision: int = 0,
) -> ROI | None:
    """Show an original/result pair and return a new selection when one is drawn."""
    selecting_roi = roi_enabled and selecting_roi
    state_key = f"{key}-view-settings"
    if state_key not in st.session_state:
        st.session_state[state_key] = ViewSettings()
    settings: ViewSettings = st.session_state[state_key]
    modes = ("원본·결과 비교", "한 장 보기")
    with st.container(horizontal=True, vertical_alignment="center", gap="medium"):
        compare = st.segmented_control(
            "보기 방식", modes, default=modes[0], required=True,
            label_visibility="collapsed", key=f"{key}-mode",
        ) == modes[0]
        with st.container(horizontal=True, width="content", vertical_alignment="center", gap="xsmall"):
            st.button("−", help="영상 축소", key=f"{key}-zoom-out",
                      on_click=_change_zoom, args=(state_key, -25), disabled=settings.zoom <= 50)
            st.markdown(f"**{settings.zoom}%**")
            st.button("+", help="영상 확대", key=f"{key}-zoom-in",
                      on_click=_change_zoom, args=(state_key, 25), disabled=settings.zoom >= 225)
        if roi_enabled and on_toggle_roi is not None:
            st.button("영역 선택 취소" if selecting_roi else "ROI 선택",
                      type="primary" if selecting_roi else "secondary",
                      key=f"{key}-select-roi", on_click=on_toggle_roi)
        st.button("초기화", help="확대·이동·밝기·대비 초기화", key=f"{key}-reset",
                  on_click=_reset_view, args=(state_key,))

    with st.expander("영상 이동 · 밝기 · 대비"):
        if settings.zoom <= 100:
            st.caption("100%를 넘겨 확대하면 영상 표시 위치를 이동할 수 있어요.")
        left, right = st.columns(2)
        for column, attr, label in ((left, "pan_x", "좌우 이동"), (right, "pan_y", "상하 이동")):
            slider_key = f"{state_key}-{attr}"
            if slider_key not in st.session_state:
                st.session_state[slider_key] = getattr(settings, attr)
            value = column.slider(label, -100, 100, step=5,
                                  disabled=settings.zoom <= 100, key=slider_key)
            setattr(settings, attr, value)
        for column, attr, label in ((left, "brightness", "밝기"), (right, "contrast", "대비")):
            slider_key = f"{state_key}-{attr}"
            if slider_key not in st.session_state:
                st.session_state[slider_key] = getattr(settings, attr)
            value = column.slider(label, 50, 170, step=5, key=slider_key)
            setattr(settings, attr, value)

    size = 710 if expanded or not compare else 512
    if compare:
        original_pane, result_pane = st.columns(2, gap="small")
    else:
        show_original = selecting_roi or active_image == "original" or result is None
        original_pane = st.container() if show_original else None
        result_pane = None if show_original else st.container()

    selection = None
    if original_pane is not None:
        with original_pane:
            st.markdown("**원본 영상**")
            if selecting_roi:
                st.caption("영상에서 드래그해 관심 영역을 선택하세요.")
                selection = _render_roi_selector(
                    original, chart_key=f"{key}-roi-selector-{roi_revision}",
                    max_size=480 if expanded or not compare else 350,
                )
            else:
                st.image(_viewport(original, roi, settings, size), width="stretch", output_format="PNG")
    if result_pane is not None:
        if result is not None:
            result_pane.markdown(f"**{result_label}**")
            result_pane.image(_viewport(result, roi, settings, size), width="stretch", output_format="PNG")
        else:
            with result_pane.container(border=True):
                st.markdown("**처리 결과**")
                st.caption("영상처리 탭에서 알고리즘을 실행하면 결과가 표시돼요.")
    return selection


def render_thumbnails(original: np.ndarray, result: np.ndarray | None, *, key: str) -> None:
    """Render quick targets for the single view and per-image memo."""
    labels = {"original": "원본", "result": "처리 결과"}
    columns = st.columns([1, 1, 2], gap="small")
    columns[0].image(_viewport(original, None, ViewSettings(), 72), width=72)
    if result is not None:
        columns[1].image(_viewport(result, None, ViewSettings(), 72), width=72)
    options = list(labels) if result is not None else ["original"]
    if st.session_state.get(key) not in options:
        st.session_state[key] = "original"
    st.segmented_control("선택 영상", options, format_func=labels.__getitem__,
                         required=True, key=key)


def _roi_image_url(image: np.ndarray) -> str:
    preview = Image.fromarray(image)
    preview.thumbnail((410, 410), Image.Resampling.BILINEAR)
    with BytesIO() as buffer:
        preview.save(buffer, format="PNG")
        return "data:image/png;base64," + b64encode(buffer.getvalue()).decode("ascii")


def _render_roi_selector(image: np.ndarray, *, chart_key: str, max_size: int) -> ROI | None:
    """Read a drag rectangle in full-resolution image coordinates."""
    rows, columns = image.shape
    display_width = max(80, round(max_size * columns / max(rows, columns)))
    display_height = max(80, round(max_size * rows / max(rows, columns)))
    interval = alt.selection_interval(name="area", encodings=["x", "y"], translate=False, zoom=False)
    # The coordinates run from the top-left to the bottom-right of the original.
    chart = (
        alt.Chart(alt.Data(values=[{"x": columns / 2, "y": rows / 2}]))
        .mark_image(url=_roi_image_url(image), aspect=False,
                    width=alt.ExprRef(expr="width"), height=alt.ExprRef(expr="height"))
        .encode(
            x=alt.X("x:Q", scale=alt.Scale(domain=[0, columns], nice=False), axis=None),
            y=alt.Y("y:Q", scale=alt.Scale(domain=[rows, 0], nice=False), axis=None),
        )
        .add_params(interval)
        .properties(width=display_width, height=display_height, padding=0)
        .configure_view(stroke=None)
    )
    event = st.altair_chart(chart, theme=None, width="content", key=chart_key,
                            on_select="rerun", selection_mode="area",
                            alt="원본 영상에서 마우스로 ROI 사각형을 선택합니다.")
    area = event.selection.get("area", {})
    if not area.get("x") or not area.get("y"):
        return None
    x0, x1 = sorted(area["x"])
    y0, y1 = sorted(area["y"])
    x0, y0 = max(0, floor(x0)), max(0, floor(y0))
    x1, y1 = min(columns, ceil(x1)), min(rows, ceil(y1))
    if x1 <= x0 or y1 <= y0:
        return None
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def render_histograms(original: ROIStatistics, result: ROIStatistics | None) -> None:
    series = [("원본", original)]
    if result is not None:
        series.append(("처리 결과", result))
    values = []
    for label, stats in series:
        counts, edges = stats["histogram"], stats["bin_edges"]
        total = sum(counts)
        for index, count in enumerate(counts):
            values.append({"영상": label, "밝기": f"{int(edges[index])}–{int(edges[index + 1] - 1)}",
                           "시작": edges[index], "비율": count / total, "픽셀 수": count})
    plot = (
        alt.Chart(alt.Data(values=values))
        .mark_bar()
        .encode(
            x=alt.X("밝기:O", sort=alt.SortField("시작", order="ascending"), title="픽셀 밝기 (0–255)",
                    axis=alt.Axis(labels=False, ticks=False)),
            xOffset=alt.XOffset("영상:N", sort=["원본", "처리 결과"]),
            y=alt.Y("비율:Q", axis=alt.Axis(format="%"), title="선택 영역 내 픽셀 비율"),
            color=alt.Color("영상:N", sort=["원본", "처리 결과"]),
            tooltip=["영상:N", "밝기:O", "픽셀 수:Q", alt.Tooltip("비율:Q", format=".1%")],
        )
        .properties(height=180)
    )
    st.altair_chart(plot, width="stretch", alt="같은 ROI의 원본과 처리 결과 밝기 분포")
