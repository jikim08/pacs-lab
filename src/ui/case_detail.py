"""Case-level Streamlit controls for comparing and analyzing a selected image."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
import streamlit as st

from src.exception import AppError, ValidationError
from src.models import Case, CaseStatus, ProcessingParameters, ROI
from src.processing.image_io import validate_grayscale_image
from src.processing.roi import analyze_roi
from src.ui.components.image_viewer import render_histograms, render_images, render_thumbnails


@dataclass(frozen=True)
class ViewerCaseOption:
    case_id: str
    patient_id: str
    status: CaseStatus
    assigned_to_user: bool = False


@dataclass(frozen=True)
class ViewerSource:
    """A source image and display metadata supplied by the authorized caller."""

    file_id: str
    name: str
    pixels: np.ndarray
    content_type: str
    modality: str | None = None
    view_position: str | None = None


@dataclass(frozen=True)
class ViewerResult:
    label: str
    pixels: np.ndarray


@dataclass
class AnalysisState:
    result: ViewerResult | None = None
    roi: ROI | None = None
    selecting_roi: bool = False
    roi_revision: int = 0


ProcessImage = Callable[[str, ProcessingParameters], ViewerResult]
PANELS = ("영상처리", "ROI 분석")


def _toggle_roi(key: str, state: AnalysisState) -> None:
    state.selecting_roi = not state.selecting_roi
    if state.selecting_roi:
        st.session_state[f"{key}-panel"] = "ROI 분석"
        st.session_state[f"{key}-active-image"] = "original"


def _set_roi(state: AnalysisState, roi: ROI | None) -> None:
    state.roi = roi
    state.selecting_roi = False
    state.roi_revision += 1


def _valid_result(result: ViewerResult, original: np.ndarray) -> None:
    validate_grayscale_image(result.pixels)
    if result.pixels.shape != original.shape:
        raise ValidationError("원본과 처리 결과의 영상 크기가 일치해야 합니다.")


def _render_processing(
    key: str,
    original: np.ndarray,
    state: AnalysisState,
    process_image: ProcessImage,
) -> None:
    st.caption("처리 범위 · 원본 영상 전체")
    algorithm = st.selectbox("알고리즘", ("CLAHE", "Median", "Canny"), key=f"{key}-algorithm")
    first, second = st.columns(2, gap="small")
    parameters: ProcessingParameters
    if algorithm == "CLAHE":
        parameters = {
            "clip_limit": first.number_input(
                "대비 제한값", min_value=0.1, max_value=10.0, value=2.0, step=0.1,
                key=f"{key}-clahe-limit",
            ),
            "tile_grid_size": second.selectbox(
                "타일 격자", (4, 8, 16), index=1, format_func=lambda value: f"{value} × {value}",
                key=f"{key}-clahe-grid",
            ),
        }
    elif algorithm == "Median":
        parameters = {"kernel_size": first.selectbox(
            "필터 크기", tuple(range(3, 16, 2)), index=1, key=f"{key}-median-kernel",
        )}
    else:
        parameters = {
            "low_threshold": first.number_input(
                "하한 임계값", min_value=0, max_value=254, value=10, key=f"{key}-canny-low",
            ),
            "high_threshold": second.number_input(
                "상한 임계값", min_value=1, max_value=255, value=30, key=f"{key}-canny-high",
            ),
        }
    if st.button("영상처리 실행", type="primary", key=f"{key}-run"):
        try:
            with st.spinner("영상 처리 중"):
                result = process_image(algorithm, parameters)
                _valid_result(result, original)
            state.result = result
            st.rerun()
        except AppError as error:
            st.error(str(error))


def _render_roi(
    key: str, original: np.ndarray, result: ViewerResult | None, state: AnalysisState,
) -> None:
    rows, columns = original.shape
    roi = state.roi
    defaults = roi or {"x": 0, "y": 0, "width": columns, "height": rows}
    st.caption("위의 ROI 선택으로 원본 영상에 그리거나, 아래에서 좌표를 직접 입력할 수 있어요.")
    with st.expander("ROI 좌표 직접 입력"):
        with st.form(f"{key}-coordinates-{state.roi_revision}"):
            left, right = st.columns(2, gap="small")
            requested: ROI = {
                "x": left.number_input("X", 0, columns - 1, defaults["x"]),
                "y": right.number_input("Y", 0, rows - 1, defaults["y"]),
                "width": left.number_input("너비", 1, columns, defaults["width"]),
                "height": right.number_input("높이", 1, rows, defaults["height"]),
            }
            if st.form_submit_button("ROI 적용"):
                try:
                    analyze_roi(original, requested)
                    _set_roi(state, requested)
                    st.rerun()
                except ValidationError as error:
                    st.error(str(error))
    if roi is None:
        return

    original_stats = analyze_roi(original, roi)
    result_stats = analyze_roi(result.pixels, roi) if result else None
    st.markdown(f"**X {roi['x']}, Y {roi['y']} · {roi['width']} × {roi['height']} px**")
    first, second = st.columns(2, gap="small")
    first.caption(f"원본 · 평균 {original_stats['mean']:.1f} · 분산 {original_stats['variance']:.1f}")
    if result_stats is not None:
        second.caption(f"처리 결과 · 평균 {result_stats['mean']:.1f} · 분산 {result_stats['variance']:.1f}")
    render_histograms(original_stats, result_stats)
    st.button("선택 해제", key=f"{key}-clear-roi", on_click=_set_roi, args=(state, None))


def _render_case_list(
    cases: Sequence[ViewerCaseOption],
    selected_id: str | None,
    on_case_change: Callable[[str], None] | None,
    key: str,
) -> None:
    with st.container(border=True):
        st.markdown("**케이스 목록**")
        query = st.text_input("환자 ID 또는 케이스 ID 검색", key=f"{key}-case-query").strip().casefold()
        assigned_only = st.segmented_control(
            "배정 범위", ("전체", "내게 배정"), default="전체", required=True,
            label_visibility="collapsed", key=f"{key}-case-assignment",
        ) == "내게 배정"
        status = st.selectbox(
            "검토 상태", ("전체 상태", "검토 대기", "검토 중", "검토 완료"),
            key=f"{key}-case-status",
        )
        matches = [
            item for item in cases
            if (not query or query in item.patient_id.casefold() or query in item.case_id.casefold())
            and (not assigned_only or item.assigned_to_user)
            and (status == "전체 상태" or item.status == status)
        ]
        if not matches:
            st.caption("조건에 맞는 케이스가 없어요." if cases else "조회 가능한 케이스가 없어요.")
            return
        with st.container(height=min(186, len(matches) * 45 + 12), border=False):
            for item in matches:
                selected = item.case_id == selected_id
                if st.button(
                    f"{item.case_id} · {item.patient_id} · {item.status}",
                    key=f"{key}-case-option-{item.case_id}", width="stretch",
                    type="primary" if selected else "secondary",
                    disabled=on_case_change is None,
                ) and not selected:
                    try:
                        if on_case_change is not None:
                            on_case_change(item.case_id)
                            st.rerun()
                    except AppError as error:
                        st.error(str(error))


def render(
    case: Case | None,
    source: ViewerSource | None,
    *,
    key: str,
    allow_analysis: bool = False,
    process_image: ProcessImage | None = None,
    stored_result: ViewerResult | None = None,
    stored_roi: ROI | None = None,
    cases: Sequence[ViewerCaseOption] | None = None,
    on_case_change: Callable[[str], None] | None = None,
    start_review: Callable[[], None] | None = None,
) -> None:
    """Render an already-authorized case passed by the application layer.

    Any processing callback is responsible for authorizing the operation itself.
    Pass only cases the current user is allowed to view. The application loads the selected case.
    """
    st.title("영상 뷰어")
    expanded = st.toggle("크게 보기", key=f"{key}-expanded")
    if expanded:
        workspace, detail = st.container(), st.container()
    else:
        workspace, detail = st.columns([3.1, 1.35], gap="medium")

    if cases is not None:
        with detail:
            _render_case_list(cases, case["case_id"] if case else None, on_case_change, key)
    if case is None:
        workspace.info("검사 목록에서 검사를 선택해 주세요.")
        return
    if source is None:
        workspace.info("선택한 검사에 등록된 영상이 없어요.")
        return

    try:
        validate_grayscale_image(source.pixels)
        if stored_result is not None:
            _valid_result(stored_result, source.pixels)
        if stored_roi is not None:
            analyze_roi(source.pixels, stored_roi)
    except ValidationError as error:
        workspace.error(str(error))
        return

    # key is scoped by the integrating screen to the authenticated user/session.
    key = f"{key}-{case['case_id']}-{source.file_id}"
    state_key = f"{key}-analysis-state"
    if state_key not in st.session_state:
        st.session_state[state_key] = AnalysisState(roi=stored_roi.copy() if stored_roi else None)
    state: AnalysisState = st.session_state[state_key]
    can_analyze = allow_analysis and case["status"] == "검토 중"
    result = (state.result or stored_result) if can_analyze else stored_result
    active_key = f"{key}-active-image"
    active = st.session_state.get(active_key, "original") if result else "original"
    selecting_roi = can_analyze and state.selecting_roi
    with detail:
        with st.container(border=True):
            st.markdown("**환자·검사 정보**")
            st.write(case["patient_id"], case["case_id"])
            st.caption(case["exam_type"])
            image_format = {"image/png": "PNG", "image/jpeg": "JPEG"}.get(
                source.content_type, source.content_type,
            )
            modalities = [image_format]
            if source.modality:
                modalities.append(source.modality)
            if source.view_position:
                modalities.append(f"촬영 방향 {source.view_position}")
            rows, columns = source.pixels.shape
            modalities.append(f"{columns} × {rows} px")
            st.caption(" · ".join(modalities))
            st.caption(f"선택 영상 · {result.label if result and active == 'result' else source.name}")

    with workspace:
        with st.container(horizontal=True, vertical_alignment="center", gap="medium"):
            with st.container(width="content", gap="xxsmall"):
                st.subheader(f"{case['case_id']} · {case['exam_type']}")
                st.caption(case["status"])
            if (start_review is not None and case["status"] == "검토 대기"
                    and st.button("검토 시작", type="primary", key=f"{key}-start")):
                try:
                    start_review()
                    st.rerun()
                except AppError as error:
                    st.error(str(error))
        roi = render_images(
            source.pixels, result.pixels if result else None,
            result.label if result else "", key=key, active_image=active,
            roi=state.roi if can_analyze else stored_roi, expanded=expanded,
            roi_enabled=can_analyze, selecting_roi=selecting_roi,
            on_toggle_roi=lambda: _toggle_roi(key, state),
            roi_revision=state.roi_revision,
        )
        if roi is not None:
            _set_roi(state, roi)
            st.rerun()
        render_thumbnails(source.pixels, result.pixels if result else None, key=active_key)

        if not can_analyze:
            st.caption("조회 전용")
            if stored_roi is not None:
                with st.expander("저장된 ROI 분석"):
                    original_stats = analyze_roi(source.pixels, stored_roi)
                    result_stats = analyze_roi(result.pixels, stored_roi) if result else None
                    render_histograms(original_stats, result_stats)
            return
        panel_key = f"{key}-panel"
        if panel_key not in st.session_state:
            st.session_state[panel_key] = PANELS[0]
        with st.container(border=True):
            panel = st.segmented_control(
                "영상 작업", PANELS, required=True, key=panel_key, label_visibility="collapsed",
            )
            if panel == "ROI 분석":
                _render_roi(key, source.pixels, result, state)
            elif process_image is not None:
                _render_processing(key, source.pixels, state, process_image)
            else:
                st.caption("처리 서비스 연결 후 실행할 수 있어요.")
