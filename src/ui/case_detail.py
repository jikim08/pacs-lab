"""Case-level Streamlit controls for comparing and analyzing a selected image."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
import streamlit as st

from src.exception import AppError, ValidationError
from src.models import Case, CaseStatus, ProcessingParameters, ProcessingResult, ReviewRecord, ROI
from src.processing.image_io import validate_grayscale_image
from src.processing.roi import analyze_roi
from src.services import processing
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
    result_id: str | None = None
    preview: processing.ProcessingPreview | None = None


@dataclass
class AnalysisState:
    result: ViewerResult | None = None
    roi: ROI | None = None
    selecting_roi: bool = False
    algorithm: str | None = None
    parameters: ProcessingParameters | None = None
    opened_result: ViewerResult | None = None
    opened_roi: ROI | None = None
    last_saved_roi: tuple[str | None, int, int, int, int] | None = None
    roi_revision: int = 0


ProcessImage = Callable[[str, ProcessingParameters], ViewerResult]
SaveImageMemo = Callable[[str, str | None, str], None]
SaveViewerResult = Callable[[ViewerResult, str, ProcessingParameters, ROI | None], ViewerResult]
SaveViewerROI = Callable[[ROI, str | None], None]
OpenViewerResult = Callable[[str], ViewerResult]
PANELS = ("영상처리", "ROI 분석", "요약 리포트", "이력")


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
    save_result: SaveViewerResult | None,
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
    pending = state.result is not None and state.result.result_id is None
    same_settings = state.algorithm == algorithm and state.parameters == parameters
    if pending and not same_settings:
        st.caption("다른 설정을 실행하면 미저장 미리보기가 교체돼요.")
    if st.button("영상처리 실행", type="primary", key=f"{key}-run", disabled=pending and same_settings):
        try:
            with st.spinner("영상 처리 중"):
                result = process_image(algorithm, parameters)
                _valid_result(result, original)
            state.result = result
            state.algorithm = algorithm
            state.parameters = dict(parameters)
            state.opened_result = None
            state.opened_roi = None
            st.rerun()
        except AppError as error:
            st.error(str(error))


    if pending:
        st.caption("현재 결과 · 미저장")
        with st.container(horizontal=True):
            if save_result is not None and st.button("처리 결과 저장", key=f"{key}-save-result"):
                try:
                    if state.result is None or state.algorithm is None or state.parameters is None:
                        raise ValidationError("저장할 처리 결과가 없어요.")
                    saved = save_result(state.result, state.algorithm, dict(state.parameters),
                                        state.roi.copy() if state.roi else None)
                    _valid_result(saved, original)
                    if not saved.result_id:
                        raise ValidationError("저장된 결과의 ID를 확인하지 못했어요.")
                    state.result = saved
                    st.rerun()
                except AppError as error:
                    st.error(str(error))
            if st.button("미리보기 지우기", key=f"{key}-discard-result"):
                state.result = None
                state.algorithm = None
                state.parameters = None
                st.rerun()
    elif state.result is not None:
        st.caption(f"현재 결과 · {state.result.label}")


def _render_roi(
    key: str, original: np.ndarray, result: ViewerResult | None, state: AnalysisState,
    save_roi: SaveViewerROI | None,
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
    with st.container(horizontal=True):
        st.button("선택 해제", key=f"{key}-clear-roi", on_click=_set_roi, args=(state, None))
        if save_roi is not None:
            result_id = result.result_id if result else None
            fingerprint = (result_id, roi["x"], roi["y"], roi["width"], roi["height"])
            if st.button(
                "ROI 기록", key=f"{key}-save-roi",
                disabled=(result is not None and result_id is None) or state.last_saved_roi == fingerprint,
                help="처리 결과의 ROI는 결과 저장 후 기록할 수 있어요.",
            ):
                try:
                    save_roi(roi.copy(), result_id)
                    state.last_saved_roi = fingerprint
                    st.rerun()
                except AppError as error:
                    st.error(str(error))


def _render_result_history(
    key: str,
    original: np.ndarray,
    records: Sequence[ProcessingResult],
    open_result: OpenViewerResult | None,
    state: AnalysisState,
    *,
    can_edit: bool,
) -> None:
    if not records:
        st.caption("저장된 처리 기록이 없어요.")
        return
    pending = can_edit and state.result is not None and state.result.result_id is None
    if pending:
        st.caption("미저장 결과를 저장하거나 지운 뒤 이전 결과를 열 수 있어요.")
    for record in sorted(records, key=lambda item: item["created_at"], reverse=True):
        result_id = record["processing_result_id"]
        with st.expander(f"{record['processing_type_id']} · {record['created_at']:%Y-%m-%d %H:%M}"):
            params = " · ".join(f"{name} {value}" for name, value in record["parameters"].items())
            st.caption(f"실행자 · {record['performed_by']} · 버전 {record['algorithm_version']}")
            if params:
                st.text(params)
            roi = record["roi"]
            if roi:
                st.caption(f"ROI · X {roi['x']}, Y {roi['y']} · {roi['width']} × {roi['height']} px")
            stats = record["statistics"]
            if stats:
                st.caption(f"평균 {stats['mean']:.1f} · 분산 {stats['variance']:.1f}")
            if open_result is not None and record["status"] == "COMPLETED":
                if st.button("이전 결과 열기", key=f"{key}-history-{result_id}", disabled=pending):
                    try:
                        result = open_result(result_id)
                        _valid_result(result, original)
                        if roi is not None:
                            analyze_roi(original, roi)
                        state.opened_result = result
                        state.opened_roi = roi
                        state.result = None
                        _set_roi(state, roi.copy() if roi else None)
                        st.rerun()
                    except AppError as error:
                        st.error(str(error))


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


def _render_image_memo(
    key: str,
    case_id: str,
    source: ViewerSource,
    active: str,
    result: ViewerResult | None,
    records: Sequence[ReviewRecord],
    save_memo: SaveImageMemo | None,
) -> None:
    result_selected = active == "result" and result is not None
    result_id = result.result_id if result_selected else None
    with st.container(border=True):
        st.markdown("**영상 메모**")
        st.caption(f"대상 · {result.label if result_selected else source.name}")
        if result_selected and result_id is None:
            st.caption("처리 결과를 저장한 뒤 해당 결과에 메모를 남길 수 있어요.")
            return

        matching = sorted(
            (
                record for record in records
                if record["kind"] == "MEMO" and record["case_id"] == case_id
                and record["file_id"] == source.file_id
                and record["processing_result_id"] == result_id
            ),
            key=lambda record: record["updated_at"],
            reverse=True,
        )
        saved = matching[0] if matching else None
        current = saved["content"] if saved else ""
        target_key = f"{key}-memo-{'stored-' + result_id if result_id else 'source'}"
        if save_memo is None:
            if current:
                st.text(current)
            else:
                st.caption("저장된 메모가 없어요.")
        else:
            text = st.text_area(
                "메모 내용", value=current, height=105,
                placeholder="선택한 영상에 대한 메모",
                label_visibility="collapsed", key=f"{target_key}-text",
            )
            if st.button(
                "메모 저장", key=f"{target_key}-save",
                width="stretch", disabled=not text.strip() or text.strip() == current,
            ):
                try:
                    save_memo(source.file_id, result_id, text.strip())
                    st.rerun()
                except AppError as error:
                    st.error(str(error))

        if saved:
            st.caption(f"작성자 · {saved['author_id']} · {saved['updated_at']:%Y-%m-%d %H:%M}")
        if len(matching) > 1:
            with st.expander("이전 메모"):
                for record in matching[1:]:
                    st.caption(f"{record['author_id']} · {record['updated_at']:%Y-%m-%d %H:%M}")
                    st.text(record["content"])


def _render_report(
    key: str,
    case_id: str,
    records: Sequence[ReviewRecord],
    save_report: Callable[[str], None] | None,
) -> None:
    reports = sorted(
        (record for record in records if record["kind"] == "REPORT" and record["case_id"] == case_id),
        key=lambda record: record["updated_at"],
        reverse=True,
    )
    saved = reports[0] if reports else None
    content = saved["content"] if saved else ""
    if save_report is None:
        if saved:
            st.text(content)
        else:
            st.caption("저장된 리포트가 없어요.")
    else:
        draft = st.text_area("케이스 요약", value=content, height=130, key=f"{key}-report-text")
        if st.button("리포트 저장", key=f"{key}-save-report", disabled=not draft.strip() or draft.strip() == content):
            try:
                save_report(draft.strip())
                st.rerun()
            except AppError as error:
                st.error(str(error))
    if saved:
        st.caption(f"작성자 · {saved['author_id']} · {saved['updated_at']:%Y-%m-%d %H:%M}")
    if len(reports) > 1:
        with st.expander("이전 리포트"):
            for record in reports[1:]:
                st.caption(f"{record['author_id']} · {record['updated_at']:%Y-%m-%d %H:%M}")
                st.text(record["content"])


def _bind_processing_service(
    uid: str, case_id: str, input_file_id: str,
) -> tuple[ProcessImage, SaveViewerResult, OpenViewerResult]:
    def run(algorithm: str, parameters: ProcessingParameters) -> ViewerResult:
        preview = processing.create_processing_preview(uid, case_id, input_file_id, algorithm, parameters)
        return ViewerResult(f"처리 결과 ({algorithm})", preview.pixels, preview=preview)

    def save(
        result: ViewerResult, _algorithm: str, _parameters: ProcessingParameters, roi: ROI | None,
    ) -> ViewerResult:
        if result.preview is None:
            raise ValidationError("저장할 처리 결과를 찾을 수 없어요.")
        record = processing.save_processing_preview(uid, case_id, input_file_id, result.preview, roi)
        return ViewerResult(result.label, result.pixels, record["processing_result_id"])

    def open_result(result_id: str) -> ViewerResult:
        saved = processing.open_processing_result(uid, case_id, input_file_id, result_id)
        return ViewerResult(f"처리 결과 ({saved.record['processing_type_id']})", saved.pixels, result_id)

    return run, save, open_result


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
    review_records: Sequence[ReviewRecord] | None = None,
    save_memo: SaveImageMemo | None = None,
    processing_records: Sequence[ProcessingResult] | None = None,
    save_result: SaveViewerResult | None = None,
    save_roi: SaveViewerROI | None = None,
    open_result: OpenViewerResult | None = None,
    start_review: Callable[[], None] | None = None,
    save_report: Callable[[str], None] | None = None,
    complete_review: Callable[[], None] | None = None,
    completion_block_reason: str | None = None,
    processing_uid: str | None = None,
) -> None:
    """Render an already-authorized case passed by the application layer.

    Any processing callback is responsible for authorizing the operation itself.
    Pass only cases the current user is allowed to view. The application loads the selected case.
    If supplied, processing_uid comes from the authenticated session and selects the real
    processing callbacks and history in place of the standalone viewer callbacks.
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

    if processing_uid is not None:
        case_id = case["case_id"]
        try:
            processing_records = processing.list_image_processing_history(processing_uid, case_id, source.file_id)
        except AppError as error:
            workspace.error(str(error))
            return
        process_image, save_result, open_result = _bind_processing_service(processing_uid, case_id, source.file_id)

    # key is scoped by the integrating screen to the authenticated user/session.
    key = f"{key}-{case['case_id']}-{source.file_id}"
    state_key = f"{key}-analysis-state"
    if state_key not in st.session_state:
        st.session_state[state_key] = AnalysisState(roi=stored_roi.copy() if stored_roi else None)
    state: AnalysisState = st.session_state[state_key]
    can_analyze = allow_analysis and case["status"] == "검토 중"
    saved_selection = state.opened_result or stored_result
    saved_roi = state.opened_roi if state.opened_result else stored_roi
    result = (state.result or saved_selection) if can_analyze else saved_selection
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

        if review_records is not None or save_memo is not None:
            _render_image_memo(
                key, case["case_id"], source, active, result, review_records or (),
                save_memo if can_analyze else None,
            )

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
            if can_analyze and complete_review is not None:
                pending = state.result is not None and state.result.result_id is None
                reason = completion_block_reason or ("처리 결과 저장 필요" if pending else None)
                with st.container(width="content", gap="xxsmall"):
                    if st.button("검토 완료", type="primary", key=f"{key}-complete", disabled=bool(reason)):
                        try:
                            complete_review()
                            st.rerun()
                        except AppError as error:
                            st.error(str(error))
                    if reason:
                        st.caption(reason)
        roi = render_images(
            source.pixels, result.pixels if result else None,
            result.label if result else "", key=key, active_image=active,
            roi=state.roi if can_analyze else saved_roi, expanded=expanded,
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
            if saved_roi is not None:
                with st.expander("저장된 ROI 분석"):
                    original_stats = analyze_roi(source.pixels, saved_roi)
                    result_stats = analyze_roi(result.pixels, saved_roi) if result else None
                    render_histograms(original_stats, result_stats)
            if review_records is not None:
                with st.expander("요약 리포트"):
                    _render_report(key, case["case_id"], review_records, None)
            if processing_records is not None:
                with st.container(border=True):
                    st.markdown("**이력**")
                    matching = [item for item in processing_records
                                if item["input_file_id"] == source.file_id
                                and item["case_id"] == case["case_id"]]
                    _render_result_history(key, source.pixels, matching, open_result, state, can_edit=False)
            return
        panel_key = f"{key}-panel"
        if panel_key not in st.session_state:
            st.session_state[panel_key] = PANELS[0]
        with st.container(border=True):
            panel = st.segmented_control(
                "영상 작업", PANELS, required=True, key=panel_key, label_visibility="collapsed",
            )
            if panel == "ROI 분석":
                _render_roi(key, source.pixels, result, state, save_roi)
            elif panel == "요약 리포트":
                _render_report(key, case["case_id"], review_records or (), save_report)
            elif panel == "이력":
                matching = [item for item in processing_records or ()
                            if item["input_file_id"] == source.file_id
                            and item["case_id"] == case["case_id"]]
                _render_result_history(key, source.pixels, matching, open_result, state, can_edit=True)
            elif process_image is not None:
                _render_processing(key, source.pixels, state, process_image, save_result)
            else:
                st.caption("처리 서비스 연결 후 실행할 수 있어요.")
