"""Streamlit tools for inspecting and comparing grayscale images."""

from dataclasses import dataclass

import numpy as np
import streamlit as st
from PIL import Image



@dataclass
class ViewSettings:
    zoom: int = 100
    pan_x: int = 0
    pan_y: int = 0
    brightness: int = 100
    contrast: int = 100


def _viewport(image: np.ndarray, settings: ViewSettings, size: int) -> Image.Image:
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
) -> None:
    """Show an original/result pair with synchronized controls."""
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
        show_original = active_image == "original" or result is None
        original_pane = st.container() if show_original else None
        result_pane = None if show_original else st.container()

    if original_pane is not None:
        original_pane.markdown("**원본 영상**")
        original_pane.image(_viewport(original, settings, size), width="stretch", output_format="PNG")
    if result_pane is not None:
        if result is not None:
            result_pane.markdown(f"**{result_label}**")
            result_pane.image(_viewport(result, settings, size), width="stretch", output_format="PNG")
        else:
            with result_pane.container(border=True):
                st.markdown("**처리 결과**")
                st.caption("영상처리 탭에서 알고리즘을 실행하면 결과가 표시돼요.")


def render_thumbnails(original: np.ndarray, result: np.ndarray | None, *, key: str) -> None:
    """Render quick targets for the single view and per-image memo."""
    labels = {"original": "원본", "result": "처리 결과"}
    columns = st.columns([1, 1, 2], gap="small")
    columns[0].image(_viewport(original, ViewSettings(), 72), width=72)
    if result is not None:
        columns[1].image(_viewport(result, ViewSettings(), 72), width=72)
    options = list(labels) if result is not None else ["original"]
    if st.session_state.get(key) not in options:
        st.session_state[key] = "original"
    st.segmented_control("선택 영상", options, format_func=labels.__getitem__,
                         required=True, key=key)
