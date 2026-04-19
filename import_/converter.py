from __future__ import annotations

import numpy as np


def convert_location(loc: tuple[float, float, float]) -> tuple[float, float, float]:
    """glTF Y-up (x,y,z) -> Blender Z-up (x,-z,y)."""
    return (loc[0], -loc[2], loc[1])


def convert_rotation(quat: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """glTF quaternion (x,y,z,w) Y-up -> Blender (w,x,y,z) Z-up."""
    gx, gy, gz, gw = quat
    return (gw, gx, -gz, gy)


def convert_scale(scale: tuple[float, float, float]) -> tuple[float, float, float]:
    """glTF (x,y,z) -> Blender (x,z,y). Self-inverse."""
    return (scale[0], scale[2], scale[1])


def convert_positions(positions: np.ndarray) -> np.ndarray:
    """Convert (N,3) positions: glTF [x,y,z] -> Blender [x,-z,y]."""
    result = positions.copy()
    y = result[:, 1].copy()
    result[:, 1] = -result[:, 2]
    result[:, 2] = y
    return result


def convert_normals(normals: np.ndarray) -> np.ndarray:
    """Same axis conversion as positions."""
    return convert_positions(normals)


def flip_uv_v(uvs: np.ndarray) -> np.ndarray:
    """glTF UV v -> Blender v (1-v). Self-inverse."""
    result = uvs.copy()
    result[:, 1] = 1.0 - result[:, 1]
    return result


def convert_location_array(locations: np.ndarray) -> np.ndarray:
    """Convert (N,3) location array: [x,y,z] -> [x,-z,y]."""
    return convert_positions(locations)


def convert_rotation_array(quats: np.ndarray) -> np.ndarray:
    """Convert (N,4) glTF [x,y,z,w] -> Blender [w,x,-z,y]."""
    return np.column_stack([quats[:, 3], quats[:, 0], -quats[:, 2], quats[:, 1]])


def convert_scale_array(scales: np.ndarray) -> np.ndarray:
    """Convert (N,3) scale: [x,y,z] -> [x,z,y]."""
    result = scales.copy()
    result[:, [1, 2]] = result[:, [2, 1]]
    return result
