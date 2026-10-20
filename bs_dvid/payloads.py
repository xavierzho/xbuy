from __future__ import annotations

import time

from bs_dvid.constants import (
    APP_KEY,
    DEFAULT_HAOTIAN_SDK,
    DEFAULT_HAOTIAN_X0,
    NORMAL_STACK_TRACE,
    TOKEN_MAGIC_DEFAULT,
)
from settings.versions import APP_VERSION
from bs_dvid.device import generate_device_fingerprint, generate_gateway_ip, get_app_signature_md5

_CAMERA_API_V1 = [
    "getCameraInfo|int#android.hardware.Camera$CameraInfo|9",
    "getEmptyParameters||9",
    "getNumberOfCameras||265",
    "open||9",
    "open|int|9",
    "openLegacy|int#int|9",
    "addCallbackBuffer|[B|17",
    "addRawImageCallbackBuffer|[B|17",
    "autoFocus|android.hardware.Camera$AutoFocusCallback|17",
    "cancelAutoFocus||17",
    "enableShutterSound|boolean|17",
    "getParameters||1",
    "lock||273",
    "previewEnabled||273",
    "reconnect||273",
    "release||17",
    "setAutoFocusMoveCallback|android.hardware.Camera$AutoFocusMoveCallback|1",
    "setDisplayOrientation|int|273",
    "setErrorCallback|android.hardware.Camera$ErrorCallback|17",
    "setFaceDetectionListener|android.hardware.Camera$FaceDetectionListener|17",
    "setOneShotPreviewCallback|android.hardware.Camera$PreviewCallback|17",
    "setParameters|android.hardware.Camera$Parameters|1",
    "setPreviewCallback|android.hardware.Camera$PreviewCallback|17",
    "setPreviewCallbackWithBuffer|android.hardware.Camera$PreviewCallback|17",
    "setPreviewDisplay|android.view.SurfaceHolder|17",
    "setPreviewTexture|android.graphics.SurfaceTexture|273",
    "setZoomChangeListener|android.hardware.Camera$OnZoomChangeListener|17",
    "startFaceDetection||17",
    "startPreview||273",
    "startSmoothZoom|int|273",
    "stopFaceDetection||17",
    "stopPreview||17",
    "stopSmoothZoom||273",
    "takePicture|android.hardware.Camera$ShutterCallback#android.hardware.Camera$PictureCallback#android.hardware.Camera$PictureCallback|17",
    "takePicture|android.hardware.Camera$ShutterCallback#android.hardware.Camera$PictureCallback#android.hardware.Camera$PictureCallback#android.hardware.Camera$PictureCallback|17",
    "unlock||273",
]

_CAMERA_API_V2 = [
    "abortCaptures||1025",
    "capture|android.hardware.camera2.CaptureRequest#android.hardware.camera2.CameraCaptureSession$CaptureCallback#android.os.Handler|1025",
    "captureBurst|java.util.List#android.hardware.camera2.CameraCaptureSession$CaptureCallback#android.os.Handler|1025",
    "captureBurstRequests|java.util.List#java.util.concurrent.Executor#android.hardware.camera2.CameraCaptureSession$CaptureCallback|1",
    "captureSingleRequest|android.hardware.camera2.CaptureRequest#java.util.concurrent.Executor#android.hardware.camera2.CameraCaptureSession$CaptureCallback|1",
    "close||1025",
    "finalizeOutputConfigurations|java.util.List|1025",
    "getDevice||1025",
    "getInputSurface||1025",
    "isReprocessable||1025",
    "prepare|android.view.Surface|1025",
    "setRepeatingBurst|java.util.List#android.hardware.camera2.CameraCaptureSession$CaptureCallback#android.os.Handler|1025",
    "setRepeatingBurstRequests|java.util.List#java.util.concurrent.Executor#android.hardware.camera2.CameraCaptureSession$CaptureCallback|1",
    "setRepeatingRequest|android.hardware.camera2.CaptureRequest#android.hardware.camera2.CameraCaptureSession$CaptureCallback#android.os.Handler|1025",
    "setSingleRepeatingRequest|android.hardware.camera2.CaptureRequest#java.util.concurrent.Executor#android.hardware.camera2.CameraCaptureSession$CaptureCallback|1",
    "stopRepeating||1025",
    "supportsOfflineProcessing|android.view.Surface|1",
]


def build_request_body(local_id: str, app_version: str) -> dict:
    return {
        "1": {
            "1": local_id,
            "3": "34",
            "4": DEFAULT_HAOTIAN_SDK,
            "5": "i茅台",
            "6": "com.moutai.mall",
            "7": get_app_signature_md5(),
            "8": app_version,
        }
    }


def build_report_events(local_id: str, app_version: str, timestamp_ms: int | None = None) -> list:
    timestamp_ms = int(timestamp_ms if timestamp_ms is not None else time.time() * 1000)
    from bs_dvid.crypto_util import md5_hash

    event_id = md5_hash(f"{local_id}{timestamp_ms}")
    event_tag = f"1|{event_id}"

    base_event = {
        "1": "i茅台",
        "2": "com.moutai.mall",
        "3": app_version,
        "4": local_id,
        "7": "0",
        "8": APP_KEY,
        "9": "haotian",
        "10": DEFAULT_HAOTIAN_SDK,
        "11": "",
        "12": "",
        "13": 1,
        "14": 1,
        "28": event_tag,
    }

    return [
        {
            **base_event,
            "5": "1003003",
            "6": timestamp_ms,
            "module_section": [{"0": "[]", "1": "[]"}],
        },
        {
            **base_event,
            "5": "1003002",
            "6": timestamp_ms - 10,
            "module_section": [{}],
        },
    ]


def build_device_env_report(
    local_id: str, timestamp_ms: int, app_version: str, gateway_ip: str | None = None
) -> dict:
    local_id_upper = local_id.split("|")[0] if "|" in local_id else local_id
    device_tag = local_id_upper + "F9"
    gateway_ip = gateway_ip or generate_gateway_ip()
    stack_trace = NORMAL_STACK_TRACE

    return {
        "1": "",
        "2": "com.moutai.mall",
        "3": app_version,
        "4": local_id,
        "5": "0",
        "7": "0",
        "8": APP_KEY,
        "9": "haotian_x0",
        "10": DEFAULT_HAOTIAN_X0,
        "11": "",
        "12": "",
        "13": "1",
        "30": "",
        "z": local_id,
        "t": device_tag,
        "6": str(timestamp_ms),
        "14": "1",
        "module_section": [
            {
                local_id: {
                    "22": [{"0": "4000", "2": str(timestamp_ms - 239), "1": ""}],
                    "86": "-1",
                    "87": stack_trace,
                    "53": "",
                    "54": "",
                    "58": "",
                    "59": "",
                    "60": "",
                    "96": device_tag,
                    "990": "",
                    "989": DEFAULT_HAOTIAN_SDK,
                    "988": [gateway_ip],
                }
            }
        ],
    }


def build_full_device_fingerprint(
    local_id: str,
    timestamp_ms: int,
    app_version: str,
    gateway_ip: str | None = None,
    *,
    device_config: dict | None = None,
    fp_data: dict | None = None,
) -> dict:
    local_id_upper = local_id.split("|")[0] if "|" in local_id else local_id
    device_tag = local_id_upper + "F9"
    if fp_data is None:
        if device_config is not None:
            from bs_dvid.device import generate_device_fingerprint_from_config

            fp_data = generate_device_fingerprint_from_config(
                device_config, gateway_ip=gateway_ip
            )
            gateway_ip = fp_data.get("gateway_ip") or gateway_ip
        else:
            fp_data = generate_device_fingerprint()
    gateway_ip = gateway_ip or fp_data.get("gateway_ip") or generate_gateway_ip()

    return {
        "1": "",
        "2": "com.moutai.mall",
        "3": app_version,
        "4": local_id,
        "5": "0",
        "7": "0",
        "8": APP_KEY,
        "9": "haotian_x0",
        "10": DEFAULT_HAOTIAN_X0,
        "11": "",
        "12": "",
        "13": "1",
        "30": "",
        "z": local_id,
        "t": device_tag,
        "6": str(timestamp_ms),
        "14": "1",
        "module_section": [
            {
                local_id: {
                    "1": fp_data["detection_results"]["1"],
                    "13": [],
                    "28": [],
                    "12": fp_data["detection_results"]["12"],
                    "69": "",
                    "9": "",
                    "2": fp_data["detection_results"]["2"],
                    "10": "",
                    "11": [],
                    "7": "1",
                    "6": "0",
                    "27": fp_data["battery_level"],
                    "17": fp_data["hardware_info"],
                    "30": "0",
                    "31": "0",
                    "32": "",
                    "46": "",
                    "70": "",
                    "50": fp_data["sensor_count"],
                    "51": "",
                    "52": "4",
                    "57": "",
                    "61": "",
                    "63": '{"8":1}',
                    "71": "",
                    "76": fp_data["build_fingerprint"],
                    "80": "[]",
                    "81": "",
                    "83": "0",
                    "84": "0",
                    "85": fp_data["network_config"],
                    "90": "",
                    "91": {"1": fp_data["certificate_hash_91"], "2": ""},
                    "92": {"1": fp_data["certificate_hash_92"], "2": ""},
                    "93": fp_data["ime_service"],
                    "97": _CAMERA_API_V1,
                    "98": _CAMERA_API_V2,
                    "988": [gateway_ip],
                    "989": DEFAULT_HAOTIAN_SDK,
                }
            }
        ],
    }


def build_sig_request_body(local_id: str) -> dict:
    return {
        "4": local_id,
        "module_section": [
            {
                "f": {
                    "1": "",
                    "2": "fe546279a62683de8ca334b673420696",
                    "3": "4e79c21720736d1fdbe6ebb60d7ce288",
                    "4": "714e9dfbf7367be71c04e07e0adc6d98",
                    "5": "fe546279a62683de8ca334b673420696",
                    "6": "714e9dfbf7367be71c04e07e0adc6d98",
                    "7": "aab3238922bcc25a6f606eb525ffdc56",
                    "9": "",
                }
            }
        ],
    }


def build_token_request_body(
    local_id: str,
    timestamp_ms: int,
    magic: str | None = None,
) -> dict:
    local_id_upper = local_id.split("|")[0] if "|" in local_id else local_id
    device_tag = local_id_upper + "F9"
    magic = magic or TOKEN_MAGIC_DEFAULT

    return {
        "1": "",
        "2": "com.moutai.mall",
        "3": APP_VERSION,
        "4": local_id,
        "5": "fCB7IHOw",
        "6": timestamp_ms,
        "7": "",
        "8": APP_KEY,
        "9": "haotian_x6",
        "10": DEFAULT_HAOTIAN_X0,
        "11": "",
        "12": "",
        "13": 1,
        "14": 1,
        "module_section": [
            {
                "token": device_tag,
                "ut": device_tag,
                "magic": magic,
                "token_rt": "",
                "mz": local_id,
                "ds": "2",
                "zid": local_id,
                "act_st": "0",
                "chn_st": "0",
                "os_ver": "34",
                "reason": {"1": 1, "2": 0, "3": timestamp_ms - 3, "4": 3},
                "tp": "3",
                "tk": device_tag + "#",
                "pd": "21043",
                "lrc": "0",
                "cc": "",
                "lre": "",
                "ipo": "1",
                "rmf": "",
                "t1": "211",
                "t2": "107",
                "t3": "",
                "15091": "0",
                "15082": f"#{timestamp_ms - 1000}#1#0",
                "15083": "",
                "15112": "###0",
                "15006": "1",
                "cuid": "",
            }
        ],
    }

