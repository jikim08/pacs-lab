"""Streamlit tools for inspecting and comparing grayscale images."""

import numpy as np
import streamlit as st
from PIL import Image



def _viewport(image: np.ndarray, size: int) -> Image.Image:
    """Render a square viewport without modifying the underlying pixels."""
    rows, columns = image.shape
    ratio = size / max(rows, columns)
    width = max(1, round(columns * ratio))
    height = max(1, round(rows * ratio))
    left = (size - width) // 2
    top = (size - height) // 2
    raw = Image.fromarray(image)
    frame = Image.new("RGB", (size, size), (7, 7, 7))
    frame.paste(raw.resize((width, height), Image.Resampling.BILINEAR), (left, top))
    return frame


def render_images(
    original: np.ndarray,
    result: np.ndarray | None,
    result_label: str,
    *,
    key: str,
    active_image: str,
    expanded: bool,
) -> None:
    """Show one chosen image or compare an original/result pair."""
    modes = ("원본·결과 비교", "한 장 보기")
    compare = st.segmented_control(
        "보기 방식", modes, default=modes[0], required=True,
        label_visibility="collapsed", key=f"{key}-mode",
    ) == modes[0]

    size = 710 if expanded or not compare else 512
    if compare:
        original_pane, result_pane = st.columns(2, gap="small")
    else:
        show_original = active_image == "original" or result is None
        original_pane = st.container() if show_original else None
        result_pane = None if show_original else st.container()

    if original_pane is not None:
        original_pane.markdown("**원본 영상**")
        original_pane.image(_viewport(original, size), width="stretch", output_format="PNG")
    if result_pane is not None:
        if result is not None:
            result_pane.markdown(f"**{result_label}**")
            result_pane.image(_viewport(result, size), width="stretch", output_format="PNG")
        else:
            with result_pane.container(border=True):
                st.markdown("**처리 결과**")
                st.caption("영상처리 탭에서 알고리즘을 실행하면 결과가 표시돼요.")


def render_thumbnails(original: np.ndarray, result: np.ndarray | None, *, key: str) -> None:
    """Render quick targets for the single view and per-image memo."""
    labels = {"original": "원본", "result": "처리 결과"}
    columns = st.columns([1, 1, 2], gap="small")
    columns[0].image(_viewport(original, 72), width=72)
    if result is not None:
        columns[1].image(_viewport(result, 72), width=72)
    options = list(labels) if result is not None else ["original"]
    if st.session_state.get(key) not in options:
        st.session_state[key] = "original"
    st.segmented_control("선택 영상", options, format_func=labels.__getitem__,
                         required=True, key=key)
