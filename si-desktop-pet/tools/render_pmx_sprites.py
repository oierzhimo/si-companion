"""Render 祀 (Endfield) 3D MMD/PMX sprites for si-desktop-pet via PySide6 OpenGL 3.3.

Note on model license:
    The source PMX model under ``art-source/mmd-chaye`` belongs to modeler
    ``茶叶味香皂`` (Aplaybox / 模之屋) and must NOT be redistributed in raw
    model form. This tool only reads the local ``art-source/mmd-chaye/祀.pmx``
    file to rasterize 2D desktop-pet animation frames into ``assets/hd``.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import struct
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QOffscreenSurface, QOpenGLContext, QSurfaceFormat
from PySide6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLFramebufferObject,
    QOpenGLFramebufferObjectFormat,
    QOpenGLFunctions_3_3_Core,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLTexture,
    QOpenGLVertexArrayObject,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PMX_DEFAULT = REPO_ROOT / 'art-source' / 'mmd-chaye' / '祀.pmx'
ASSETS_DIR = REPO_ROOT / 'assets'

MASTER_W = 1536
MASTER_H = 1664
BASELINE_BOTTOM_PX = 1616  # Matches existing desktop pet ground contact at y=1616/1664.

# OpenGL constants missing from some stripped bindings
GL_TRIANGLES = 0x0004
GL_FLOAT = 0x1406
GL_DEPTH_TEST = 0x0B71
GL_LEQUAL = 0x0203
GL_BLEND = 0x0BE2
GL_CULL_FACE = 0x0B44
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_ONE = 0x0001
GL_COLOR_BUFFER_BIT = 0x4000
GL_DEPTH_BUFFER_BIT = 0x0100

VERT_SHADER = """
#version 330 core
layout(location = 0) in vec3 inPos;
layout(location = 1) in vec3 inNormal;
layout(location = 2) in vec2 inUV;

uniform mat4 uMVP;
uniform mat4 uModelView;

out vec3 vNormal;
out vec2 vUV;
out vec3 vWorldPos;
out vec3 vViewPos;
out vec3 vViewNormal;

void main() {
    gl_Position = uMVP * vec4(inPos, 1.0);
    vNormal = inNormal;
    vUV = inUV;
    vWorldPos = inPos;
    vViewPos = (uModelView * vec4(inPos, 1.0)).xyz;
    vViewNormal = mat3(uModelView) * inNormal;
}
"""

GBUF_FRAG_SHADER = """
#version 330 core
in vec2 vUV;
in vec3 vViewPos;
in vec3 vViewNormal;

uniform sampler2D uTex;
uniform vec4 uDiffuse;
uniform int uMatKind;

out vec4 fragColor;

void main() {
    float alpha = texture(uTex, vUV).a * uDiffuse.a;
    if (alpha < 0.65) discard;
    // Normalize view-space depth cleanly across [20.0 .. 90.0] into 16-bit RG
    float d = clamp((vViewPos.z - 20.0) / 70.0, 0.0, 1.0);
    float d_scaled = d * 255.0;
    float hi = floor(d_scaled) / 255.0;
    float lo = fract(d_scaled);
    vec3 vn = normalize(vViewNormal);
    if (!gl_FrontFacing) vn = -vn;
    // Pack matKind into high 3 bits of B and view-space normal Z into low 5 bits of B
    float nzq = floor(clamp(vn.z * 0.5 + 0.5, 0.0, 0.999) * 31.0);
    float b_packed = (float(uMatKind) * 32.0 + nzq) / 255.0;
    fragColor = vec4(hi, lo, b_packed, 1.0);
}
"""

FRAG_SHADER = """
#version 330 core
in vec3 vNormal;
in vec2 vUV;
in vec3 vWorldPos;
in vec3 vViewPos;
in vec3 vViewNormal;

uniform sampler2D uTex;
uniform sampler2D uNormTex;
uniform sampler2D uPackTex;
uniform sampler2D uSpecTex;
uniform int uHasNorm;
uniform int uHasPack;
uniform int uHasSpec;
uniform float uNormFlipY;
uniform vec4 uDiffuse;
uniform int uMatKind; // 0=face/eye/brow, 1=skin, 2=cloth, 3=hair, 4=tail
uniform int uPass;    // 0=opaque cutoff, 1=translucent edge blend
uniform vec3 uCamDir; // Normalized direction toward camera (-Z after yaw)
uniform float uPivotZ; // View-space Z of character root pivot for front-to-back layering

out vec4 fragColor;

vec3 adjust_sat(vec3 c, float sat) {
    float l = dot(c, vec3(0.299, 0.587, 0.114));
    return clamp(mix(vec3(l), c, sat), 0.0, 1.0);
}

vec3 perturb_normal(vec3 N, vec3 p, vec2 uv, vec3 texN, float strength) {
    vec3 dp1 = dFdx(p);
    vec3 dp2 = dFdy(p);
    vec2 duv1 = dFdx(uv);
    vec2 duv2 = dFdy(uv);
    vec3 dp2perp = cross(dp2, N);
    vec3 dp1perp = cross(N, dp1);
    vec3 T = dp2perp * duv1.x + dp1perp * duv2.x;
    vec3 B = dp2perp * duv1.y + dp1perp * duv2.y;
    float lenMax = max(dot(T, T), dot(B, B));
    if (lenMax < 1e-8) return N;
    float invmax = inversesqrt(lenMax);
    mat3 TBN = mat3(T * invmax, B * invmax, N);
    vec3 mapN = normalize(vec3(texN.x * strength, texN.y * strength * uNormFlipY, max(texN.z, 0.05)));
    return normalize(TBN * mapN);
}

void main() {
    vec4 texel = texture(uTex, vUV);
    float alpha = texel.a * uDiffuse.a;
    if (uPass == 0) {
        if (alpha < 0.85) discard;
        alpha = 1.0;
    } else {
        if (alpha <= 0.03 || alpha >= 0.85) discard;
    }

    vec3 raw = texel.rgb * uDiffuse.rgb;
    float lum = dot(raw, vec3(0.299, 0.587, 0.114));
    float maxC = max(raw.r, max(raw.g, raw.b));
    float minC = min(raw.r, min(raw.g, raw.b));
    float chroma = maxC - minC;

    vec3 N_geom = normalize(vNormal);
    bool isBackSide = !gl_FrontFacing;
    if (isBackSide) N_geom = -N_geom;

    vec3 N = N_geom;
    if (uHasNorm == 1) {
        vec3 nSamp = texture(uNormTex, vUV).rgb;
        // BC5/RG packed normal maps have B ~ 0 -> reconstruct Z
        float nx = nSamp.r * 2.0 - 1.0;
        float ny = nSamp.g * 2.0 - 1.0;
        float nz = (nSamp.b < 0.05) ? sqrt(max(0.0, 1.0 - nx * nx - ny * ny)) : (nSamp.b * 2.0 - 1.0);
        float nStrength = (uMatKind == 3) ? 1.15 : ((uMatKind == 1) ? 0.55 : 1.65);
        N = perturb_normal(N_geom, vWorldPos, vUV, vec3(nx, ny, nz), nStrength);
    }

    vec4 packSamp = (uHasPack == 1) ? texture(uPackTex, vUV) : vec4(0.0, 0.0, 1.0, 0.5);
    float bakedAO = (uHasPack == 1) ? packSamp.b : 1.0;
    float metallic = (uHasPack == 1) ? packSamp.r : 0.0;

    // Default Front-Above Studio Key Light (bright crown/chest/front, sculpted downward shadow!)
    vec3 L = normalize(vec3(-0.24, 0.72, -0.65));
    vec3 V = normalize(uCamDir);
    vec3 H = normalize(L + V);
    float ndl = dot(N, L);
    float ndl_geom = dot(N_geom, L);
    float ndh = clamp(dot(N, H), 0.0, 1.0);
    float viewDot = clamp(dot(N_geom, V), 0.0, 1.0);
    float rim = pow(1.0 - viewDot, 3.0);

    // Overall top-to-bottom light falloff + front-to-back view depth separation
    float relZ = vViewPos.z - uPivotZ;
    float rearFactor = smoothstep(0.08, 2.3, relZ);
    float depthLight = mix(1.09, 0.68, rearFactor);
    vec3 depthTint = mix(vec3(1.00, 1.00, 1.00), vec3(0.90, 0.95, 1.02), rearFactor);
    float innerShade = isBackSide ? 0.76 : 1.0;
    // Gentle top-down light energy so feet/lower skirt feel grounded while face/crown/shoulders glow
    float topDown = mix(0.92, 1.06, smoothstep(2.0, 18.0, vWorldPos.y));

    vec3 outCol;
    if (uMatKind == 0) {
        // Face, iris, highlights, eyelashes: radiant porcelain face (clean chin, gentle bangs shadow, soft cheek wrap)
        float isSkin = smoothstep(0.42, 0.62, lum);
        float bangsShadow = smoothstep(18.78, 19.38, vWorldPos.y) * smoothstep(-0.35, -1.1, vWorldPos.z);
        vec3 paleFace = adjust_sat(clamp(raw * 1.25 + vec3(0.045, 0.04, 0.04), 0.0, 1.0), 0.58);
        vec3 eyeRich = adjust_sat(pow(max(raw, vec3(0.001)), vec3(1.16)), 1.30);
        vec3 base = mix(eyeRich, paleFace, isSkin);
        float cel = smoothstep(-0.34, 0.14, ndl_geom);
        vec3 lightEnv = mix(vec3(0.90, 0.86, 0.90), vec3(1.045, 1.03, 1.015), cel);
        lightEnv *= mix(1.0, 0.89, bangsShadow * isSkin);
        outCol = mix(base, base * lightEnv, isSkin * 0.90);
    } else if (uMatKind == 1) {
        // Mat[7] body: skin with collarbone/knee normal detail + cobalt gloves
        float isWarmSkin = smoothstep(-0.02, 0.06, raw.r - raw.b) * smoothstep(0.35, 0.55, lum);
        vec3 paleSkin = adjust_sat(clamp(raw * 1.26 + vec3(0.045, 0.04, 0.04), 0.0, 1.0), 0.54);
        vec3 cobaltGlove = adjust_sat(clamp(raw * vec3(0.88, 1.06, 1.26), 0.0, 1.0), 1.48);
        vec3 base = mix(cobaltGlove, paleSkin, isWarmSkin);

        float underSkirt = smoothstep(9.0, 11.4, vWorldPos.y) * (1.0 - smoothstep(11.4, 12.2, vWorldPos.y));
        float underChin = smoothstep(17.0, 17.7, vWorldPos.y) * (1.0 - smoothstep(17.7, 18.2, vWorldPos.y));
        float ao = mix(1.0, 0.74, underSkirt * isWarmSkin) * mix(1.0, 0.74, underChin * isWarmSkin);
        float cel = smoothstep(-0.08, 0.22, ndl);
        vec3 celTint = mix(vec3(0.75, 0.71, 0.77), vec3(1.05, 1.03, 1.01), cel) * ao;
        outCol = base * celTint * mix(1.0, depthLight, 0.75) * topDown + vec3(0.05, 0.04, 0.04) * rim * cel;
    } else if (uMatKind == 3) {
        // Hair: airy luminous celadon-sage with crisp strand boundaries, top-front light gradient, & angel-ring sheen!
        float isDarkStreak = (1.0 - smoothstep(0.24, 0.42, lum)) * (1.0 - smoothstep(0.08, 0.20, chroma));
        vec3 darkHair = pow(max(raw, vec3(0.001)), vec3(1.38)) * 0.70;
        vec3 sageHair = adjust_sat(clamp(raw * vec3(1.06, 1.08, 1.03) + vec3(0.04, 0.045, 0.03), 0.0, 1.0), 0.60);
        vec3 base = mix(sageHair, darkHair, isDarkStreak);

        // Clean crisp strand-edge darkening on silhouette/grazing locks without muddy darkness across face bangs
        float lockEdge = smoothstep(0.05, 0.34, viewDot);
        float aoShade = mix(0.72, 1.12, clamp(bakedAO, 0.0, 1.0));
        // Clear light-to-shadow gradient from front-above key light
        float cel = smoothstep(-0.14, 0.24, ndl * 0.65 + ndl_geom * 0.35);
        float crownGlow = smoothstep(18.2, 20.6, vWorldPos.y);
        float specMask = (uHasSpec == 1) ? texture(uSpecTex, vUV).r : 0.7;
        float ringBand = exp(-pow((vWorldPos.y - 19.65) * 2.1, 2.0)) * smoothstep(-0.1, 0.35, ndl_geom);
        float angel = (pow(ndh, 24.0) * 0.20 + ringBand * 0.32) * specMask * cel;

        vec3 celTint = mix(vec3(0.65, 0.77, 0.74), vec3(1.15, 1.16, 1.11), cel);
        celTint *= mix(0.88, 1.10, crownGlow) * mix(0.78, 1.02, lockEdge) * aoShade;
        outCol = base * celTint * mix(1.0, depthLight, 0.85) * depthTint + vec3(0.28, 0.33, 0.28) * angel + vec3(0.09, 0.13, 0.11) * rim * (1.0 - rearFactor * 0.5);
    } else if (uMatKind == 4) {
        // Dragon tail & jade orb: sculpted scale normals + depth separation
        float isDark = (1.0 - smoothstep(0.22, 0.40, lum)) * (1.0 - smoothstep(0.08, 0.18, chroma));
        vec3 base = mix(adjust_sat(raw * 1.08, 1.02), pow(max(raw, vec3(0.001)), vec3(1.38)) * 0.72, isDark);
        float cel = smoothstep(-0.08, 0.24, ndl);
        float spec = pow(ndh, 24.0) * 0.24 * cel;
        vec3 celTint = mix(vec3(0.62, 0.72, 0.72), vec3(1.07, 1.08, 1.05), cel) * mix(0.72, 1.03, bakedAO);
        outCol = base * celTint * mix(0.80, 1.0, 1.0 - rearFactor * 0.45) + vec3(0.22, 0.38, 0.33) * spec + vec3(0.08, 0.16, 0.14) * rim;
    } else {
        // Clothes (Mat 8/9/10): crisp snow-white front dress with 2K abdomen wrinkles/pleats + recessed outer coat volume
        float isBlack = (1.0 - smoothstep(0.16, 0.32, lum)) * (1.0 - smoothstep(0.07, 0.16, chroma));
        float isLowChromaWhite = smoothstep(0.54, 0.70, lum) * (1.0 - smoothstep(0.09, 0.22, chroma));
        float isVivid = smoothstep(0.12, 0.26, chroma);

        vec3 inkBlack = pow(max(raw, vec3(0.001)), vec3(1.58)) * 0.58;
        vec3 pearlWhite = adjust_sat(clamp((raw - vec3(0.38)) * 1.48 + vec3(0.76), 0.0, 1.0), 0.08);
        vec3 calmSlate = adjust_sat(raw * 1.02, 0.55);
        vec3 vividAccent = adjust_sat(clamp(raw * 1.15, 0.0, 1.0), 1.38);
        vec3 base = mix(calmSlate, vividAccent, isVivid);
        base = mix(base, inkBlack, isBlack);
        base = mix(base, pearlWhite, isLowChromaWhite);

        // Strong sculpted 2K cavity AO + deep abdomen skirt crease lighting!
        float ao = mix(0.66, 1.06, clamp(bakedAO, 0.0, 1.0));
        float cel = smoothstep(-0.14, 0.22, ndl);
        float softWrap = smoothstep(-0.42, 0.62, ndl);
        float gloss = pow(ndh, mix(36.0, 16.0, max(isBlack, metallic))) * mix(0.10, 0.44, max(isBlack, metallic)) * cel;
        vec3 celTint = mix(vec3(0.67, 0.72, 0.80), vec3(1.11, 1.11, 1.10), cel * 0.75 + softWrap * 0.25);
        float layerScale = mix(1.0, depthLight, 0.94) * innerShade * ao * topDown;
        outCol = base * celTint * layerScale * depthTint + vec3(0.32, 0.35, 0.38) * gloss + vec3(0.06, 0.08, 0.09) * rim * (1.0 - rearFactor * 0.5);
    }

    fragColor = vec4(clamp(outCol, 0.0, 1.0), alpha);
}
"""


def rot_xyz(rx_deg: float = 0.0, ry_deg: float = 0.0, rz_deg: float = 0.0) -> np.ndarray:
    rx, ry, rz = map(math.radians, (rx_deg, ry_deg, rz_deg))
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]], dtype=np.float32)
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=np.float32)
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]], dtype=np.float32)
    return Ry @ Rx @ Rz


def scale_rotation(R: np.ndarray, rate: float) -> np.ndarray:
    """Scale a 3x3 rotation matrix by ``rate`` (supports negative grant rates)."""
    if abs(rate) < 1e-6:
        return np.eye(3, dtype=np.float32)
    if abs(rate - 1.0) < 1e-6:
        return R
    if abs(rate + 1.0) < 1e-6:
        return R.T
    tr = float(np.trace(R))
    cos_a = max(-1.0, min(1.0, (tr - 1.0) * 0.5))
    angle = math.acos(cos_a)
    if angle < 1e-5:
        return np.eye(3, dtype=np.float32)
    denom = 2.0 * math.sin(angle)
    axis = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]], dtype=np.float64) / denom
    a = angle * rate
    ca, sa = math.cos(a), math.sin(a)
    ux, uy, uz = axis
    K = np.array([[0, -uz, uy], [uz, 0, -ux], [-uy, ux, 0]], dtype=np.float64)
    return (np.eye(3) * ca + (1.0 - ca) * np.outer(axis, axis) + K * sa).astype(np.float32)


class PMXModel:
    def __init__(self, path: Path):
        data = path.read_bytes()
        pos = 4

        def read(fmt: str):
            nonlocal pos
            sz = struct.calcsize(fmt)
            val = struct.unpack_from(fmt, data, pos)
            pos += sz
            return val[0] if len(val) == 1 else val

        def read_text(enc: int) -> str:
            nonlocal pos
            length = read('<i')
            if length <= 0:
                return ''
            raw = data[pos:pos + length]
            pos += length
            return raw.decode('utf-16-le' if enc == 0 else 'utf-8', errors='replace')

        def read_idx(sz: int, signed: bool = True) -> int:
            if sz == 1:
                return read('<b' if signed else '<B')
            if sz == 2:
                return read('<h' if signed else '<H')
            if sz == 4:
                return read('<i' if signed else '<I')
            raise ValueError(sz)

        read('<f')  # version
        g_cnt = read('<B')
        g = [read('<B') for _ in range(g_cnt)]
        enc, add_vec4, v_sz, tex_sz, mat_sz, bone_sz, morph_sz, rb_sz = g[:8]
        for _ in range(4):
            read_text(enc)

        # Vertices
        v_cnt = read('<i')
        positions = np.zeros((v_cnt, 3), dtype=np.float32)
        normals = np.zeros((v_cnt, 3), dtype=np.float32)
        uvs = np.zeros((v_cnt, 2), dtype=np.float32)
        b_idx = np.zeros((v_cnt, 4), dtype=np.int32)
        b_wgt = np.zeros((v_cnt, 4), dtype=np.float32)

        for i in range(v_cnt):
            px, py, pz, nx, ny, nz, u, v = read('<8f')
            positions[i] = (px, py, pz)
            normals[i] = (nx, ny, nz)
            uvs[i] = (u, v)
            pos += 16 * add_vec4
            deform = read('<B')
            if deform == 0:
                b0 = read_idx(bone_sz)
                b_idx[i, 0] = max(0, b0)
                b_wgt[i, 0] = 1.0
            elif deform == 1:
                b0 = read_idx(bone_sz)
                b1 = read_idx(bone_sz)
                w0 = read('<f')
                b_idx[i, 0] = max(0, b0)
                b_idx[i, 1] = max(0, b1)
                b_wgt[i, 0] = w0
                b_wgt[i, 1] = 1.0 - w0
            elif deform in (2, 4):
                bs = [max(0, read_idx(bone_sz)) for _ in range(4)]
                ws = read('<4f')
                s = sum(ws)
                b_idx[i] = bs
                b_wgt[i] = [w / s if s > 1e-6 else (1.0 if j == 0 else 0.0) for j, w in enumerate(ws)]
            elif deform == 3:
                b0 = read_idx(bone_sz)
                b1 = read_idx(bone_sz)
                w0 = read('<f')
                pos += 36
                b_idx[i, 0] = max(0, b0)
                b_idx[i, 1] = max(0, b1)
                b_wgt[i, 0] = w0
                b_wgt[i, 1] = 1.0 - w0
            read('<f')

        self.positions = positions
        self.normals = normals
        self.uvs = uvs
        self.b_idx = b_idx
        self.b_wgt = b_wgt

        # Faces
        idx_cnt = read('<i')
        fmt_map = {1: '<B', 2: '<H', 4: '<I'}
        raw_indices = np.frombuffer(data, dtype=fmt_map[v_sz], count=idx_cnt, offset=pos).astype(np.int32)
        pos += idx_cnt * v_sz
        self.faces = raw_indices.reshape((-1, 3))

        # Texture paths
        tex_cnt = read('<i')
        self.tex_paths = [read_text(enc).replace('\\', '/') for _ in range(tex_cnt)]
        self.tex_resolved: List[Path] = []
        for rel in self.tex_paths:
            p = path.parent / rel
            if not p.exists():
                p = path.parent / 'other tex' / Path(rel).name
            self.tex_resolved.append(p)

        # Materials
        mat_cnt = read('<i')
        self.materials = []
        tri_offset = 0
        for _ in range(mat_cnt):
            m_jp = read_text(enc)
            read_text(enc)
            diff = read('<4f')
            read('<3f')
            read('<f')
            read('<3f')
            flag = read('<B')
            read('<4f')
            read('<f')
            tex_i = read_idx(tex_sz)
            read_idx(tex_sz)
            read('<B')
            t_type = read('<B')
            read('<B') if t_type == 1 else read_idx(tex_sz)
            read_text(enc)
            surf_cnt = read('<i')
            tri_cnt = surf_cnt // 3
            self.materials.append({
                'name': m_jp,
                'diffuse': diff,
                'tex': tex_i,
                'double_sided': bool(flag & 0x01),
                'tri_start': tri_offset,
                'tri_end': tri_offset + tri_cnt,
            })
            tri_offset += tri_cnt

        # Bones
        bone_cnt = read('<i')
        self.bones = []
        self.bone_name_to_idx: Dict[str, int] = {}
        for i in range(bone_cnt):
            b_jp = read_text(enc)
            read_text(enc)
            b_pos = np.array(read('<3f'), dtype=np.float32)
            parent = read_idx(bone_sz)
            read('<i')
            b_flag = read('<H')
            if b_flag & 0x0001:
                read_idx(bone_sz)
            else:
                read('<3f')
            grant_parent, grant_rate = -1, 0.0
            if b_flag & (0x0100 | 0x0200):
                grant_parent = read_idx(bone_sz)
                grant_rate = read('<f')
            if b_flag & 0x0400:
                read('<3f')
            if b_flag & 0x0800:
                read('<3f')
                read('<3f')
            if b_flag & 0x2000:
                read('<i')
            if b_flag & 0x0020:
                read_idx(bone_sz)
                read('<i')
                read('<f')
                links = read('<i')
                for _ in range(links):
                    read_idx(bone_sz)
                    if read('<B'):
                        read('<3f')
                        read('<3f')
            self.bones.append({
                'name': b_jp,
                'pos': b_pos,
                'parent': parent,
                'flag': b_flag,
                'grant_parent': grant_parent,
                'grant_rate': grant_rate,
            })
            self.bone_name_to_idx[b_jp] = i

        # Morphs (type 1 = vertex)
        morph_cnt = read('<i')
        self.morphs: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
        for _ in range(morph_cnt):
            mo_jp = read_text(enc)
            read_text(enc)
            read('<B')
            m_type = read('<B')
            cnt = read('<i')
            if m_type == 1:
                v_indices = np.zeros(cnt, dtype=np.int32)
                v_offsets = np.zeros((cnt, 3), dtype=np.float32)
                for j in range(cnt):
                    v_indices[j] = read_idx(v_sz, signed=False)
                    v_offsets[j] = read('<3f')
                self.morphs[mo_jp] = (v_indices, v_offsets)
            else:
                for _ in range(cnt):
                    if m_type == 0:
                        read_idx(morph_sz)
                        read('<f')
                    elif m_type == 2:
                        read_idx(bone_sz)
                        read('<3f')
                        read('<4f')
                    elif m_type in (3, 4, 5, 6, 7):
                        read_idx(v_sz, signed=False)
                        read('<4f')
                    elif m_type == 8:
                        read_idx(mat_sz)
                        read('<B')
                        pos += 112
                    elif m_type == 9:
                        read_idx(morph_sz)
                        read('<f')
                    elif m_type == 10:
                        read_idx(rb_sz)
                        read('<B')
                        pos += 24

    def skin(self, bone_rots: Dict[str, Tuple[float, float, float]], bone_trans: Dict[str, Tuple[float, float, float]] | None = None, morph_weights: Dict[str, float] | None = None) -> Tuple[np.ndarray, np.ndarray]:
        bone_trans = bone_trans or {}
        morph_weights = morph_weights or {}

        verts = self.positions.copy()
        for m_name, w in morph_weights.items():
            if w and m_name in self.morphs:
                idx, off = self.morphs[m_name]
                verts[idx] += off * float(w)

        n_bones = len(self.bones)
        local_R = [np.eye(3, dtype=np.float32) for _ in range(n_bones)]
        local_t = [np.zeros(3, dtype=np.float32) for _ in range(n_bones)]
        for b_name, angles in bone_rots.items():
            bi = self.bone_name_to_idx.get(b_name)
            if bi is not None:
                local_R[bi] = rot_xyz(*angles)
        for b_name, tr in bone_trans.items():
            bi = self.bone_name_to_idx.get(b_name)
            if bi is not None:
                local_t[bi] = np.array(tr, dtype=np.float32)

        eff_R = [r.copy() for r in local_R]
        for i, b in enumerate(self.bones):
            gp = b['grant_parent']
            if (b['flag'] & 0x0100) and 0 <= gp < n_bones:
                eff_R[i] = scale_rotation(eff_R[gp], b['grant_rate']) @ eff_R[i]

        skin_M = np.zeros((n_bones, 4, 4), dtype=np.float32)
        world_M = [np.eye(4, dtype=np.float32) for _ in range(n_bones)]
        for i, b in enumerate(self.bones):
            p = b['parent']
            pos_i = b['pos']
            if 0 <= p < i:
                rel = pos_i - self.bones[p]['pos']
                parent_M = world_M[p]
            else:
                rel = pos_i
                parent_M = np.eye(4, dtype=np.float32)
            loc = np.eye(4, dtype=np.float32)
            loc[:3, :3] = eff_R[i]
            loc[:3, 3] = rel + local_t[i]
            wm = parent_M @ loc
            world_M[i] = wm
            inv_bind = np.eye(4, dtype=np.float32)
            inv_bind[:3, 3] = -pos_i
            skin_M[i] = wm @ inv_bind

        M_v = (
            skin_M[self.b_idx[:, 0]] * self.b_wgt[:, 0, None, None]
            + skin_M[self.b_idx[:, 1]] * self.b_wgt[:, 1, None, None]
            + skin_M[self.b_idx[:, 2]] * self.b_wgt[:, 2, None, None]
            + skin_M[self.b_idx[:, 3]] * self.b_wgt[:, 3, None, None]
        )
        v_homo = np.concatenate([verts, np.ones((len(verts), 1), dtype=np.float32)], axis=1)[:, :, None]
        skinned = (M_v @ v_homo)[:, :3, 0]
        skinned_n = (M_v[:, :3, :3] @ self.normals[:, :, None])[:, :, 0]
        n_len = np.linalg.norm(skinned_n, axis=1, keepdims=True)
        skinned_n = skinned_n / np.maximum(n_len, 1e-6)
        return skinned, skinned_n


class GLPMXRenderer:
    def __init__(self, model: PMXModel, width: int = MASTER_W, height: int = MASTER_H, samples: int = 8):
        self.model = model
        self.width = width
        self.height = height

        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        fmt.setAlphaBufferSize(8)

        self.ctx = QOpenGLContext()
        self.ctx.setFormat(fmt)
        if not self.ctx.create():
            raise RuntimeError('Failed to create OpenGL 3.3 context')
        self.surface = QOffscreenSurface()
        self.surface.setFormat(fmt)
        self.surface.create()
        if not self.ctx.makeCurrent(self.surface):
            raise RuntimeError('Failed to make OpenGL context current')

        self.gl = QOpenGLFunctions_3_3_Core()
        if not self.gl.initializeOpenGLFunctions():
            raise RuntimeError('Failed to initialize OpenGL 3.3 Core functions')

        fbo_fmt = QOpenGLFramebufferObjectFormat()
        fbo_fmt.setAttachment(QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
        fbo_fmt.setSamples(samples)
        self.msaa_fbo = QOpenGLFramebufferObject(width, height, fbo_fmt)

        resolve_fmt = QOpenGLFramebufferObjectFormat()
        self.resolve_fbo = QOpenGLFramebufferObject(width, height, resolve_fmt)

        gbuf_fmt = QOpenGLFramebufferObjectFormat()
        gbuf_fmt.setAttachment(QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
        self.gbuf_fbo = QOpenGLFramebufferObject(width, height, gbuf_fmt)

        self.prog = QOpenGLShaderProgram()
        if not self.prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, VERT_SHADER):
            raise RuntimeError(self.prog.log())
        if not self.prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, FRAG_SHADER):
            raise RuntimeError(self.prog.log())
        if not self.prog.link():
            raise RuntimeError(self.prog.log())

        self.gbuf_prog = QOpenGLShaderProgram()
        if not self.gbuf_prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, VERT_SHADER):
            raise RuntimeError(self.gbuf_prog.log())
        if not self.gbuf_prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, GBUF_FRAG_SHADER):
            raise RuntimeError(self.gbuf_prog.log())
        if not self.gbuf_prog.link():
            raise RuntimeError(self.gbuf_prog.log())

        # Upload textures (use Linear mipmaps; keep auxiliary normal/pack textures Linear)
        self.gl_textures: List[QOpenGLTexture] = []
        for p in model.tex_resolved:
            img = QImage(str(p)).convertToFormat(QImage.Format.Format_RGBA8888)
            tex = QOpenGLTexture(img, QOpenGLTexture.MipMapGeneration.GenerateMipMaps)
            tex.setMinMagFilters(QOpenGLTexture.Filter.LinearMipMapLinear, QOpenGLTexture.Filter.Linear)
            tex.setWrapMode(QOpenGLTexture.WrapMode.Repeat)
            self.gl_textures.append(tex)

        # Load auxiliary engine PBR maps (_N / _HN, _P, _ST) from art-source/mmd-chaye/other tex
        other_dir = PMX_DEFAULT.parent / 'other tex'
        self.aux_tex_cache: Dict[str, QOpenGLTexture] = {}
        def load_aux(fname: str) -> QOpenGLTexture | None:
            if fname in self.aux_tex_cache:
                return self.aux_tex_cache[fname]
            fp = other_dir / fname
            if not fp.is_file():
                return None
            q = QImage(str(fp)).convertToFormat(QImage.Format.Format_RGBA8888)
            t = QOpenGLTexture(q, QOpenGLTexture.MipMapGeneration.GenerateMipMaps)
            t.setMinMagFilters(QOpenGLTexture.Filter.LinearMipMapLinear, QOpenGLTexture.Filter.Linear)
            t.setWrapMode(QOpenGLTexture.WrapMode.Repeat)
            self.aux_tex_cache[fname] = t
            return t

        self.mat_aux: Dict[int, Tuple[QOpenGLTexture | None, QOpenGLTexture | None, QOpenGLTexture | None]] = {}
        aux_map = {
            'T_actor_jsspsi_body_01_D.png': ('T_actor_jsspsi_body_01_N.png', None, None),
            'T_actor_jsspsi_cloth_01_D.png': ('T_actor_jsspsi_cloth_01_N.png', 'T_actor_jsspsi_cloth_01_P.png', None),
            'T_actor_jsspsi_cloth_02_D.png': ('T_actor_jsspsi_cloth_02_N.png', 'T_actor_jsspsi_cloth_02_P.png', None),
            'T_actor_jsspsi_cloth_04_D.png': ('T_actor_jsspsi_cloth_04_N.png', 'T_actor_jsspsi_cloth_04_P.png', None),
            'T_actor_jsspsi_hair_01_D.png': ('T_actor_jsspsi_hair_01_HN.png', 'T_actor_jsspsi_hair_01_P.png', 'T_actor_jsspsi_hair_01_ST.png'),
        }
        for i, mat in enumerate(model.materials):
            ti = mat['tex']
            if 0 <= ti < len(model.tex_paths):
                base_name = Path(model.tex_paths[ti]).name
                n_fn, p_fn, s_fn = aux_map.get(base_name, (None, None, None))
                self.mat_aux[i] = (
                    load_aux(n_fn) if n_fn else None,
                    load_aux(p_fn) if p_fn else None,
                    load_aux(s_fn) if s_fn else None,
                )
            else:
                self.mat_aux[i] = (None, None, None)

        # Precompute flat triangle index array & UV array for glDrawArrays
        self.flat_idx = model.faces.reshape(-1)
        flat_uv = model.uvs[self.flat_idx].astype(np.float32)

        self.vao = QOpenGLVertexArrayObject()
        self.vao.create()
        self.vao.bind()

        self.pos_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.pos_vbo.create()
        self.pos_vbo.bind()
        self.pos_vbo.allocate(len(self.flat_idx) * 3 * 4)

        self.norm_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.norm_vbo.create()
        self.norm_vbo.bind()
        self.norm_vbo.allocate(len(self.flat_idx) * 3 * 4)

        self.uv_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.uv_vbo.create()
        self.uv_vbo.bind()
        uv_bytes = flat_uv.tobytes()
        self.uv_vbo.allocate(uv_bytes, len(uv_bytes))

        self.prog.bind()
        self.pos_vbo.bind()
        self.prog.enableAttributeArray(0)
        self.prog.setAttributeBuffer(0, GL_FLOAT, 0, 3, 12)
        self.norm_vbo.bind()
        self.prog.enableAttributeArray(1)
        self.prog.setAttributeBuffer(1, GL_FLOAT, 0, 3, 12)
        self.uv_vbo.bind()
        self.prog.enableAttributeArray(2)
        self.prog.setAttributeBuffer(2, GL_FLOAT, 0, 2, 8)
        self.vao.release()

        # Material classification for toon shader + draw order
        self.mat_kinds = {}
        for i, mat in enumerate(model.materials):
            name = mat['name']
            if i in (0, 1, 2, 3, 5, 6):
                self.mat_kinds[i] = 0
            elif name == '肌':
                self.mat_kinds[i] = 1
            elif name == '发':
                self.mat_kinds[i] = 3
            elif name == '尾鬃':
                self.mat_kinds[i] = 4
            else:
                self.mat_kinds[i] = 2

        # Draw face/eyes before hair/clothes where appropriate, skipping stencil/0-alpha mats
        self.draw_mats = [
            i for i, mat in enumerate(model.materials)
            if mat['diffuse'][3] > 0.01 and mat['name'] not in ('表情', '目影', '发影')
        ]

    def render(
        self,
        bone_rots: Dict[str, Tuple[float, float, float]],
        bone_trans: Dict[str, Tuple[float, float, float]] | None = None,
        morph_weights: Dict[str, float] | None = None,
        yaw_deg: float = 0.0,
        pitch_deg: float = 0.0,
        world_y_shift: float = 0.0,
        y_view_range: float = 24.4,
        bottom_px: float = BASELINE_BOTTOM_PX,
    ) -> Image.Image:
        self.ctx.makeCurrent(self.surface)
        skinned, skinned_n = self.model.skin(bone_rots, bone_trans, morph_weights)
        # Auto-ground lowest vertex onto y=0 so every pose meets BASELINE_BOTTOM_PX cleanly
        min_y = float(skinned[:, 1].min())
        skinned[:, 1] += (-min_y) + world_y_shift

        # Update dynamic pos + normal VBOs
        flat_pos = skinned[self.flat_idx].astype(np.float32).tobytes()
        flat_n = skinned_n[self.flat_idx].astype(np.float32).tobytes()
        self.pos_vbo.bind()
        self.pos_vbo.write(0, flat_pos, len(flat_pos))
        self.norm_vbo.bind()
        self.norm_vbo.write(0, flat_n, len(flat_n))

        # Build realistic 3D portrait perspective MVP (FOV=24°) so torso/legs/sleeves have natural depth:
        #   Camera sits at z = -cam_dist looking toward +Z, centered around target_y.
        #   Choose target_y and cam_dist so y=[0 .. y_view_range] maps cleanly to [bottom_ndc .. bottom_ndc+2.0] at z=0.
        fov_rad = math.radians(24.0)
        aspect = self.width / self.height
        f = 1.0 / math.tan(fov_rad * 0.5)
        half_h_at_z0 = y_view_range * 0.5
        cam_dist = half_h_at_z0 * f
        bottom_ndc = 1.0 - 2.0 * (bottom_px / self.height)
        # At z=0, ndc_y should equal (y - target_y) / half_h_at_z0 -> y=0 maps to bottom_ndc => -target_y/half_h_at_z0 = bottom_ndc
        target_y = -bottom_ndc * half_h_at_z0

        R_cam = rot_xyz(pitch_deg, yaw_deg, 0.0)
        M_pivot = np.eye(4, dtype=np.float32)
        M_pivot[:3, :3] = R_cam
        pivot = np.array([0.0, 9.5, 0.0], dtype=np.float32)
        M_pivot[:3, 3] = pivot - R_cam @ pivot

        # View matrix: translate world by (0, -target_y, +cam_dist) so camera is at origin looking along +Z (z_cam > 0)
        V = np.array([
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, -target_y],
            [0.0, 0.0, 1.0, cam_dist],
            [0.0, 0.0, 0.0, 1.0],
        ], dtype=np.float32)

        # Perspective projection for +Z forward: x_ndc = (f/aspect)*x/z, y_ndc = f*y/z, z_ndc = (near+far)/(far-near) + (-2*near*far)/(far-near)/z
        z_near, z_far = 15.0, 110.0
        a_z = (z_far + z_near) / (z_far - z_near)
        b_z = -(2.0 * z_far * z_near) / (z_far - z_near)
        P = np.array([
            [f / aspect, 0.0, 0.0, 0.0],
            [0.0, f, 0.0, 0.0],
            [0.0, 0.0, a_z, b_z],
            [0.0, 0.0, 1.0, 0.0],
        ], dtype=np.float32)
        mv = V @ M_pivot
        mvp = P @ mv
        from PySide6.QtGui import QMatrix4x4, QVector3D, QVector4D
        qm = QMatrix4x4(*mvp.flatten().tolist())
        qmv = QMatrix4x4(*mv.flatten().tolist())

        cam_dir = (R_cam.T @ np.array([0.0, 0.0, -1.0], dtype=np.float32)).tolist()

        gl = self.gl
        loc_tex = self.prog.uniformLocation('uTex')
        loc_norm = self.prog.uniformLocation('uNormTex')
        loc_pack = self.prog.uniformLocation('uPackTex')
        loc_spec = self.prog.uniformLocation('uSpecTex')
        loc_has_norm = self.prog.uniformLocation('uHasNorm')
        loc_has_pack = self.prog.uniformLocation('uHasPack')
        loc_has_spec = self.prog.uniformLocation('uHasSpec')
        loc_norm_flipy = self.prog.uniformLocation('uNormFlipY')
        loc_pass = self.prog.uniformLocation('uPass')
        loc_kind = self.prog.uniformLocation('uMatKind')

        self.msaa_fbo.bind()
        gl.glViewport(0, 0, self.width, self.height)
        gl.glClearColor(0.0, 0.0, 0.0, 0.0)
        gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        gl.glDisable(GL_CULL_FACE)
        gl.glEnable(GL_DEPTH_TEST)
        gl.glDepthFunc(GL_LEQUAL)

        self.prog.bind()
        self.prog.setUniformValue('uMVP', qm)
        self.prog.setUniformValue('uModelView', qmv)
        self.prog.setUniformValue('uCamDir', QVector3D(*cam_dir))
        gl.glUniform1f(self.prog.uniformLocation('uPivotZ'), float(cam_dist))
        gl.glUniform1f(loc_norm_flipy, -1.0)
        gl.glUniform1i(loc_tex, 0)
        gl.glUniform1i(loc_norm, 1)
        gl.glUniform1i(loc_pack, 2)
        gl.glUniform1i(loc_spec, 3)
        self.vao.bind()

        def bind_mat_textures(mat_idx: int):
            mat = self.model.materials[mat_idx]
            ti = mat['tex']
            self.gl_textures[ti].bind(0)
            nt, pt, st = self.mat_aux.get(mat_idx, (None, None, None))
            if nt is not None:
                nt.bind(1)
                gl.glUniform1i(loc_has_norm, 1)
            else:
                gl.glUniform1i(loc_has_norm, 0)
            if pt is not None:
                pt.bind(2)
                gl.glUniform1i(loc_has_pack, 1)
            else:
                gl.glUniform1i(loc_has_pack, 0)
            if st is not None:
                st.bind(3)
                gl.glUniform1i(loc_has_spec, 1)
            else:
                gl.glUniform1i(loc_has_spec, 0)

        # Pass 0: Opaque & solid alpha cutoff with depth write ON
        gl.glDisable(GL_BLEND)
        gl.glDepthMask(True)
        gl.glUniform1i(loc_pass, 0)
        for i in self.draw_mats:
            mat = self.model.materials[i]
            ti = mat['tex']
            if not (0 <= ti < len(self.gl_textures)):
                continue
            bind_mat_textures(i)
            self.prog.setUniformValue('uDiffuse', QVector4D(*mat['diffuse']))
            gl.glUniform1i(loc_kind, self.mat_kinds[i])
            first = mat['tri_start'] * 3
            cnt = (mat['tri_end'] - mat['tri_start']) * 3
            gl.glDrawArrays(GL_TRIANGLES, first, cnt)

        # Pass 1: Semi-transparent wisps (hair tips, sheer socks, cloth lace, dragon tail fins)
        gl.glEnable(GL_BLEND)
        gl.glBlendFuncSeparate(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA, GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
        gl.glDepthMask(False)
        gl.glUniform1i(loc_pass, 1)
        for i in self.draw_mats:
            mat = self.model.materials[i]
            ti = mat['tex']
            if not (0 <= ti < len(self.gl_textures)):
                continue
            bind_mat_textures(i)
            self.prog.setUniformValue('uDiffuse', QVector4D(*mat['diffuse']))
            gl.glUniform1i(loc_kind, self.mat_kinds[i])
            first = mat['tri_start'] * 3
            cnt = (mat['tri_end'] - mat['tri_start']) * 3
            gl.glDrawArrays(GL_TRIANGLES, first, cnt)

        gl.glDepthMask(True)
        self.vao.release()
        self.prog.release()
        self.msaa_fbo.release()

        # Pass 2: Lightweight depth+matKind+normal G-buffer for crisp hair lock & coat/skirt overlap lines
        self.gbuf_fbo.bind()
        gl.glViewport(0, 0, self.width, self.height)
        gl.glClearColor(1.0, 1.0, 0.0, 0.0)
        gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        gl.glDisable(GL_BLEND)
        gl.glEnable(GL_DEPTH_TEST)
        gl.glDepthFunc(GL_LEQUAL)
        gl.glDepthMask(True)

        self.gbuf_prog.bind()
        self.gbuf_prog.setUniformValue('uMVP', qm)
        self.gbuf_prog.setUniformValue('uModelView', qmv)
        gl.glUniform1i(self.gbuf_prog.uniformLocation('uTex'), 0)
        loc_gk = self.gbuf_prog.uniformLocation('uMatKind')
        self.vao.bind()
        for i in self.draw_mats:
            mat = self.model.materials[i]
            ti = mat['tex']
            if not (0 <= ti < len(self.gl_textures)):
                continue
            self.gl_textures[ti].bind(0)
            self.gbuf_prog.setUniformValue('uDiffuse', QVector4D(*mat['diffuse']))
            gl.glUniform1i(loc_gk, self.mat_kinds[i])
            first = mat['tri_start'] * 3
            cnt = (mat['tri_end'] - mat['tri_start']) * 3
            gl.glDrawArrays(GL_TRIANGLES, first, cnt)
        self.vao.release()
        self.gbuf_prog.release()
        self.gbuf_fbo.release()

        QOpenGLFramebufferObject.blitFramebuffer(self.resolve_fbo, self.msaa_fbo)
        qimg = self.resolve_fbo.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        arr = np.frombuffer(qimg.bits(), dtype=np.uint8).reshape((self.height, self.width, 4)).copy()

        # Compute crisp depth-jump & material-boundary contours (separating hair strands, buns, coat & skirt!)
        gimg = self.gbuf_fbo.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        garr = np.frombuffer(gimg.bits(), dtype=np.uint8).reshape((self.height, self.width, 4))
        depth = (garr[:, :, 0].astype(np.float32) * 256.0 + garr[:, :, 1].astype(np.float32)) / 65535.0 * 70.0
        b_raw = garr[:, :, 2]
        mat_id = (b_raw >> 5)
        nz_val = (b_raw & 31).astype(np.float32) / 31.0
        g_alpha = garr[:, :, 3] > 128

        edge = np.zeros((self.height, self.width), dtype=np.float32)
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            d_pad = np.pad(depth, ((1, 1), (1, 1)), mode='edge')
            m_pad = np.pad(mat_id, ((1, 1), (1, 1)), mode='edge')
            n_pad = np.pad(nz_val, ((1, 1), (1, 1)), mode='edge')
            a_pad = np.pad(g_alpha, ((1, 1), (1, 1)), mode='constant', constant_values=False)

            d_nb = d_pad[1 + dy:1 + dy + self.height, 1 + dx:1 + dx + self.width]
            m_nb = m_pad[1 + dy:1 + dy + self.height, 1 + dx:1 + dx + self.width]
            n_nb = n_pad[1 + dy:1 + dy + self.height, 1 + dx:1 + dx + self.width]
            a_nb = a_pad[1 + dy:1 + dy + self.height, 1 + dx:1 + dx + self.width]

            # Lower threshold on hair (mat_id == 3) so bangs/buns/locks get crisp individual contours!
            is_hair = (mat_id == 3) | (m_nb == 3)
            d_thresh = np.where(is_hair, 0.085, 0.18)
            fg_step = np.where(g_alpha & a_nb, np.clip((d_nb - depth - d_thresh) * 0.95, 0.0, 1.0), 0.0)
            # Crease line on sharp normal turns (hair bun silhouette, coat lapel, skirt pleats)
            n_step = np.where(g_alpha & a_nb & (mat_id == m_nb), np.clip((np.abs(nz_val - n_nb) - 0.26) * 2.2, 0.0, 0.75), 0.0)
            # Crisp subtle separation where hair crosses face/skin/clothes
            mat_step = np.where(g_alpha & a_nb & is_hair & (mat_id != m_nb), 0.65, 0.0)
            edge = np.maximum(edge, np.maximum(fg_step, np.maximum(n_step, mat_step)))

        # Subtle cool-jade ink on hair boundaries, dark ink-slate on cloth/body overlaps
        is_hair_px = (mat_id == 3)[:, :, None]
        ink_rgb = np.where(
            is_hair_px,
            np.array([42.0, 62.0, 56.0], dtype=np.float32),
            np.array([38.0, 46.0, 56.0], dtype=np.float32),
        )
        ink_alpha = (edge * np.where(is_hair_px[:, :, 0], 0.56, 0.48))[:, :, None]
        valid = (arr[:, :, 3:4] > 20)
        arr_f = arr[:, :, :3].astype(np.float32)
        arr_f = np.where(valid, arr_f * (1.0 - ink_alpha) + ink_rgb * ink_alpha, arr_f)
        arr[:, :, :3] = np.clip(arr_f, 0, 255).astype(np.uint8)
        return Image.fromarray(arr, 'RGBA')


# ---- Natural Idle & Chain Draping Helpers ----

def base_drape() -> Dict[str, Tuple[float, float, float]]:
    """Default natural Endfield stance matching authentic in-game silhouette."""
    rots: Dict[str, Tuple[float, float, float]] = {}
    # Gentle relaxed A-stance matching game idle (-35° rest -> ~ -60° natural arm hang)
    rots['左肩'] = (0.0, 0.0, -3.0)
    rots['右肩'] = (0.0, 0.0, 3.0)
    rots['左腕'] = (4.0, 0.0, -25.0)
    rots['右腕'] = (4.0, 0.0, 25.0)
    rots['左ひじ'] = (14.0, 0.0, -6.0)
    rots['右ひじ'] = (14.0, 0.0, 6.0)
    rots['左手首'] = (0.0, 0.0, 6.0)
    rots['右手首'] = (0.0, 0.0, -6.0)

    # Graceful A-frame drape for wide cape sleeves (hugging leg line gently, not stiffly flared)
    rots['L_sleeveD_01_jnt_1'] = (4.0, 0.0, 14.0)
    rots['L_sleeveD_01_jnt_2'] = (0.0, 0.0, 4.0)
    rots['R_sleeveD_01_jnt_1'] = (4.0, 0.0, -14.0)
    rots['R_sleeveD_01_jnt_2'] = (0.0, 0.0, -4.0)

    # Yellow shoulder ribbon & sword-tassel drape smoothly along outer sleeve edge (matching authentic photo)
    rots['ribbonB_01_jnt_1'] = (2.0, -6.0, -12.0)
    rots['ribbonB_01_jnt_2'] = (0.0, 0.0, -6.0)
    rots['ribbonB_01_jnt_3'] = (0.0, 0.0, -4.0)
    rots['ribbonB_01_jnt_6'] = (0.0, 0.0, 6.0)
    for k in range(1, 5):
        rots[f'ribbonF_01_jnt_{k}'] = (0.0, -4.0, -3.5)

    # Right arm back ribbon (ribbonC): tuck gently downward
    rots['ribbonC_01_jnt_1'] = (-4.0, 0.0, -14.0)
    rots['ribbonC_01_jnt_2'] = (-2.0, 0.0, -8.0)

    # Back twin long hair drape smoothly
    rots['L_hairE_jnt1'] = (-8.0, 0.0, -6.0)
    rots['R_hairE_jnt1'] = (-8.0, 0.0, 6.0)
    rots['L_hairE_jnt6'] = (-4.0, 0.0, 4.0)
    rots['R_hairE_jnt6'] = (-4.0, 0.0, -4.0)

    # Curve jade dragon tail gently behind her left ankle (+X, screen right) matching in-game stance!
    rots['tail_jnt2'] = (6.0, 10.0, 0.0)
    rots['tail_jnt4'] = (6.0, 14.0, 0.0)
    rots['tail_jnt6'] = (4.0, 14.0, 0.0)
    rots['tail_jnt8'] = (2.0, 10.0, 0.0)
    rots['tail_jnt10'] = (0.0, 6.0, 0.0)
    rots['tail_jnt12'] = (-2.0, -4.0, 0.0)
    rots['tail_jnt14'] = (-4.0, -8.0, 0.0)
    rots['tail_jnt16'] = (-4.0, -8.0, 0.0)
    return rots


def merge_rots(base: Dict[str, Tuple[float, float, float]], overrides: Dict[str, Tuple[float, float, float]]) -> Dict[str, Tuple[float, float, float]]:
    out = dict(base)
    out.update(overrides)
    return out


def build_state_specs() -> Dict[str, List[dict]]:
    """Return frame pose definitions for all 14 states in assets/animations.json."""
    base = base_drape()
    specs: Dict[str, List[dict]] = {}

    # 1. idle (6 frames): 0=calm stand, 1-3=blink sequence used by controller.py, 4-5=gentle breathe/tail sway
    idle_frames = []
    blink_map = [0.0, 0.55, 1.0, 0.40, 0.0, 0.0]
    for i in range(6):
        ph = i / 6.0 * 2.0 * math.pi
        tail_sway = math.sin(ph) * 4.0
        r = merge_rots(base, {
            '上半身': (math.sin(ph) * 0.8, 0.0, 0.0),
            '頭': (-2.5 + math.sin(ph) * 0.6, -1.5, -0.8),
            'tail_jnt8': (2.0, 10.0 + tail_sway, 0.0),
        })
        # Neutral clean anime mouth (no 口内 gap dots)
        m = {}
        if blink_map[i] > 0:
            m['まばたき'] = blink_map[i]
        idle_frames.append({'rots': r, 'morphs': m, 'yaw': 0.0})
    specs['idle'] = idle_frames

    # 2. walk-right (8 frames) & walk-left (8 frames)
    for side, yaw in (('walk-right', -52.0), ('walk-left', 52.0)):
        frames = []
        for i in range(8):
            t = i / 8.0 * 2.0 * math.pi
            s = math.sin(t)
            c = math.cos(t)
            l_hip = s * 22.0
            r_hip = -s * 22.0
            l_knee = max(0.0, -c) * 26.0 + 4.0
            r_knee = max(0.0, c) * 26.0 + 4.0
            r = merge_rots(base, {
                '左足': (l_hip, 0.0, 1.5),
                '右足': (r_hip, 0.0, -1.5),
                '左ひざ': (-l_knee, 0.0, 0.0),
                '右ひざ': (-r_knee, 0.0, 0.0),
                '左足首': (l_knee * 0.45 - l_hip * 0.35, 0.0, 0.0),
                '右足首': (r_knee * 0.45 - r_hip * 0.35, 0.0, 0.0),
                '上半身': (-3.0, -s * 4.0, 0.0),
                '頭': (2.0, -yaw * 0.22, 0.0),
                '左腕': (-s * 18.0 + 3.0, 0.0, -34.0),
                '右腕': (s * 18.0 + 3.0, 0.0, 34.0),
                '左ひじ': (14.0, 0.0, -6.0),
                '右ひじ': (14.0, 0.0, 6.0),
                'tail_jnt8': (4.0, -12.0 + s * 6.0, 0.0),
            })
            frames.append({'rots': r, 'morphs': {}, 'yaw': yaw})
        specs[side] = frames

    # 3. running-right (8 frames) & running-left (8 frames)
    for side, yaw in (('running-right', -58.0), ('running-left', 58.0)):
        frames = []
        for i in range(8):
            t = i / 8.0 * 2.0 * math.pi
            s = math.sin(t)
            c = math.cos(t)
            l_hip = s * 34.0
            r_hip = -s * 34.0
            l_knee = max(0.0, -c) * 44.0 + 8.0
            r_knee = max(0.0, c) * 44.0 + 8.0
            r = merge_rots(base, {
                '左足': (l_hip, 0.0, 1.5),
                '右足': (r_hip, 0.0, -1.5),
                '左ひざ': (-l_knee, 0.0, 0.0),
                '右ひざ': (-r_knee, 0.0, 0.0),
                '上半身': (-10.0, -s * 6.0, 0.0),
                '頭': (7.0, -yaw * 0.25, 0.0),
                '左腕': (-s * 28.0 + 6.0, 0.0, -30.0),
                '右腕': (s * 28.0 + 6.0, 0.0, 30.0),
                '左ひじ': (28.0, 0.0, -10.0),
                '右ひじ': (28.0, 0.0, 10.0),
                'tail_jnt4': (14.0, -10.0 + s * 8.0, 0.0),
            })
            frames.append({'rots': r, 'morphs': {'口角上げ': 0.35}, 'yaw': yaw})
        specs[side] = frames

    # 4. waving (4 frames, settles on frame 3) -> graceful cheer wave, sleeve & ribbon draping straight down
    wave_angles = [(-35.0, 22.0, -1.5), (-52.0, 18.0, -4.0), (-62.0, 28.0, -3.5), (-56.0, 20.0, -4.5)]
    specs['waving'] = []
    for i, (arm_z, elbow_z, head_tilt) in enumerate(wave_angles):
        counter = -arm_z * 1.15
        w_ov = {
            '右肩': (0.0, 0.0, -10.0),
            '右腕': (18.0, 8.0, arm_z),
            '右ひじ': (28.0, 0.0, -elbow_z),
            '右腕捩': (0.0, -25.0, 0.0),
            '右手首': (0.0, -15.0, 0.0),
            '左腕': (3.0, 0.0, -34.0),
            '頭': (-2.0, -4.0, head_tilt),
            'R_sleeveD_01_jnt_1': (12.0, 0.0, counter * 0.50),
            'R_sleeveD_01_jnt_2': (6.0, 0.0, counter * 0.35),
            'R_sleeveD_01_jnt_3': (4.0, 0.0, counter * 0.25),
            'ribbonC_01_jnt_1': (0.0, 0.0, counter * 0.45 + 15.0),
            'ribbonC_01_jnt_2': (0.0, 0.0, counter * 0.35 + 10.0),
            'ribbonC_01_jnt_3': (0.0, 0.0, counter * 0.25),
        }
        r = merge_rots(base, w_ov)
        specs['waving'].append({'rots': r, 'morphs': {'笑い': 0.35 if i >= 1 else 0.15, '口角上げ': 0.45}})

    # 5. sitting (4 frames, settles on frame 3) -> knees bent, cape & tail swept gently back so shoes sit flat at bottom
    sit_prog = [0.28, 0.58, 0.86, 1.0]
    specs['sitting'] = []
    for p in sit_prog:
        r = merge_rots(base, {
            '腰': (-4.0 * p, 0.0, 0.0),
            '上半身': (-4.0 * p, -6.0 * p, 0.0),
            '頭': (3.0 * p, 6.0 * p, -3.5 * p),
            '左足': (74.0 * p, -6.0 * p, 4.0 * p),
            '右足': (74.0 * p, 6.0 * p, -4.0 * p),
            '左ひざ': (-80.0 * p, 0.0, 0.0),
            '右ひざ': (-80.0 * p, 0.0, 0.0),
            '左足首': (-12.0 * p, 0.0, 0.0),
            '右足首': (-12.0 * p, 0.0, 0.0),
            '左腕': (22.0 * p + 3.0, 12.0 * p, -32.0),
            '右腕': (22.0 * p + 3.0, -12.0 * p, 32.0),
            '左ひじ': (48.0 * p + 8.0, 0.0, -18.0 * p),
            '右ひじ': (48.0 * p + 8.0, 0.0, 18.0 * p),
            # Sweep cape sleeves gently backward/upward when seated so they rest at seat level instead of dangling past soles
            'L_sleeveD_01_jnt_1': (-24.0 * p + 6.0, 0.0, 32.0),
            'L_sleeveD_01_jnt_5': (-16.0 * p, 0.0, 0.0),
            'R_sleeveD_01_jnt_1': (-24.0 * p + 6.0, 0.0, -32.0),
            'R_sleeveD_01_jnt_5': (-16.0 * p, 0.0, 0.0),
            'tail_jnt2': (2.0, -14.0, 0.0),
            'tail_jnt4': (-6.0 * p + 6.0, -18.0, 0.0),
            'tail_jnt6': (-10.0 * p + 4.0, -16.0, 0.0),
            'tail_jnt8': (-8.0 * p, -10.0, 0.0),
        })
        specs['sitting'].append({'rots': r, 'morphs': {'口角上げ': 0.25 * p}})

    # 6. sleeping (4 frames, settles on frame 3) -> peaceful curled seated doze resting squarely at bottom
    slp_prog = [0.35, 0.65, 0.90, 1.0]
    specs['sleeping'] = []
    for p in slp_prog:
        r = merge_rots(base, {
            '腰': (-6.0 * p, 0.0, 0.0),
            '上半身': (-8.0 * p, 0.0, 4.0 * p),
            '上半身2': (-5.0 * p, 0.0, 4.0 * p),
            '首': (-5.0 * p, 0.0, 5.0 * p),
            '頭': (-6.0 * p, -4.0 * p, 9.0 * p),
            'L_hairE_jnt1': (10.0 * p, 0.0, -10.0),
            'R_hairE_jnt1': (10.0 * p, 0.0, 10.0),
            '左足': (76.0 * p, -8.0 * p, 5.0 * p),
            '右足': (76.0 * p, 6.0 * p, -5.0 * p),
            '左ひざ': (-86.0 * p, 0.0, 0.0),
            '右ひざ': (-86.0 * p, 0.0, 0.0),
            '左腕': (26.0 * p, 14.0 * p, -34.0),
            '右腕': (26.0 * p, -14.0 * p, 34.0),
            '左ひじ': (56.0 * p, 0.0, -24.0 * p),
            '右ひじ': (56.0 * p, 0.0, 24.0 * p),
            'L_sleeveD_01_jnt_1': (-26.0 * p + 6.0, 0.0, 32.0),
            'L_sleeveD_01_jnt_5': (-18.0 * p, 0.0, 0.0),
            'R_sleeveD_01_jnt_1': (-26.0 * p + 6.0, 0.0, -32.0),
            'R_sleeveD_01_jnt_5': (-18.0 * p, 0.0, 0.0),
            'tail_jnt2': (2.0, -14.0, 0.0),
            'tail_jnt4': (-6.0 * p + 6.0, -18.0, 0.0),
            'tail_jnt6': (-10.0 * p + 4.0, -16.0, 0.0),
            'tail_jnt8': (-8.0 * p, -10.0, 0.0),
        })
        specs['sleeping'].append({'rots': r, 'morphs': {'なごみ': min(1.0, 0.4 + 0.6 * p)}, 'yaw': -18.0 * p})

    # 7. jumping (5 frames, settles on frame 4 airborne)
    jmp_params = [
        (0.5, 10.0, -28.0, -10.0),
        (2.0, -8.0, -32.0, -30.0),
        (2.8, -12.0, -38.0, -38.0),
        (2.5, -8.0, -34.0, -32.0),
        (2.0, -5.0, -26.0, -26.0),
    ]
    specs['jumping'] = []
    for lift, body_x, knee_x, arm_raise in jmp_params:
        r = merge_rots(base, {
            '上半身': (body_x, 0.0, 0.0),
            '頭': (-body_x * 0.5, 0.0, -2.0),
            '左足': (16.0, 0.0, 5.0),
            '右足': (24.0, 0.0, -5.0),
            '左ひざ': (knee_x, 0.0, 0.0),
            '右ひざ': (knee_x * 1.15, 0.0, 0.0),
            '左腕': (8.0, 0.0, arm_raise),
            '右腕': (8.0, 0.0, -arm_raise),
            'L_sleeveD_01_jnt_1': (10.0, 0.0, 28.0),
            'R_sleeveD_01_jnt_1': (10.0, 0.0, -28.0),
        })
        specs['jumping'].append({'rots': r, 'morphs': {'口角上げ': 0.35}, 'y_shift': lift})

    # 8. review (6 frames, settles on frame 5) -> observant head tilt, right hand raised toward chin/collarbone
    specs['review'] = []
    for i in range(6):
        p = min(1.0, (i + 1) / 4.0)
        r = merge_rots(base, {
            '上半身': (-2.0 * p, -5.0 * p, 0.0),
            '頭': (4.0 * p, 6.0 * p, -6.0 * p),
            '右腕': (32.0 * p + 3.0, -22.0 * p, 32.0),
            '右ひじ': (86.0 * p + 10.0, 0.0, 46.0 * p),
            '右手首': (0.0, 10.0 * p, 14.0 * p),
            '右人指１': (0.0, 0.0, 15.0 * p),
            '右中指１': (0.0, 0.0, 28.0 * p),
            '右中指２': (0.0, 0.0, 30.0 * p),
            '右薬指１': (0.0, 0.0, 35.0 * p),
            '右薬指２': (0.0, 0.0, 32.0 * p),
            '右小指１': (0.0, 0.0, 38.0 * p),
            'R_sleeveD_01_jnt_1': (-12.0 * p + 6.0, 0.0, -28.0),
            '左腕': (8.0, 4.0, -35.0),
            '左ひじ': (20.0, 0.0, -10.0),
        })
        specs['review'].append({'rots': r, 'morphs': {'じと目': 0.12}})

    # 9. running (6 frames, settles on frame 5) -> mapped to "think" / "special"
    specs['running'] = []
    for i in range(6):
        p = min(1.0, (i + 1) / 3.5)
        aha = 1.0 if i >= 4 else 0.0
        r = merge_rots(base, {
            '上半身': (0.0, 5.0 * p, 0.0),
            '頭': (-4.0 * aha + 5.0 * (1 - aha), -8.0 * p, 6.0 * p),
            '左腕': (30.0 * p + 3.0, 20.0 * p, -30.0),
            '左ひじ': (82.0 * p + 10.0, 0.0, -42.0 * p),
            'L_sleeveD_01_jnt_1': (-12.0 * p + 6.0, 0.0, 28.0),
            '右腕': (10.0, 0.0, 34.0 - 8.0 * aha),
        })
        m = {'笑い': 0.32, '口角上げ': 0.42} if aha else {'真面目': 0.25}
        specs['running'].append({'rots': r, 'morphs': m})

    # 10. waiting (6 frames) -> gentle idle weight shift + soft blink
    specs['waiting'] = []
    for i in range(6):
        ph = i / 6.0 * 2.0 * math.pi
        r = merge_rots(base, {
            '腰': (0.0, math.sin(ph) * 4.0, math.sin(ph) * 2.0),
            '上半身': (1.0, -math.sin(ph) * 3.0, -math.sin(ph) * 2.0),
            '頭': (2.0, math.sin(ph) * 4.0, -math.sin(ph) * 3.0),
            'tail_jnt8': (4.0, -12.0 + math.cos(ph) * 7.0, 0.0),
        })
        m = {}
        if i == 3:
            m['まばたき'] = 0.9
        specs['waiting'].append({'rots': r, 'morphs': m})

    # 11. failed (8 frames) -> slightly flustered / hands raised in front
    specs['failed'] = []
    for i in range(8):
        s = math.sin(i / 8.0 * 2.0 * math.pi)
        r = merge_rots(base, {
            '上半身': (-4.0, s * 5.0, s * 2.0),
            '頭': (-5.0, -s * 6.0, 5.0),
            '左腕': (14.0, 0.0, -24.0),
            '右腕': (14.0, 0.0, 24.0),
            '左ひじ': (42.0, 0.0, -18.0),
            '右ひじ': (42.0, 0.0, 18.0),
        })
        specs['failed'].append({'rots': r, 'morphs': {'困る': 0.85, 'ω': 0.35}})

    # 12. look (16 frames, index 0..15 clockwise from top: 0=UP, 4=RIGHT(+X), 8=DOWN, 12=LEFT(-X))
    specs['look'] = []
    for i in range(16):
        ang = math.radians(i * 22.5)
        sx_dir = math.sin(ang)   # +1 = screen right (+X), -1 = screen left (-X)
        sy_dir = math.cos(ang)   # +1 = screen up (+Y), -1 = screen down (-Y)
        head_yaw = -sx_dir * 18.0
        head_pitch = sy_dir * 12.0
        head_roll = -sx_dir * 4.0
        eye_yaw = -sx_dir * 8.0
        eye_pitch = sy_dir * 7.0
        r = merge_rots(base, {
            '上半身2': (sy_dir * 1.5, -sx_dir * 4.0, 0.0),
            '頭': (head_pitch, head_yaw, head_roll),
            '両目': (eye_pitch, eye_yaw, 0.0),
        })
        specs['look'].append({'rots': r, 'morphs': {}})

    return specs


def main() -> int:
    parser = argparse.ArgumentParser(description='Render 祀 3D MMD sprites for si-desktop-pet.')
    parser.add_argument('--pmx', type=Path, default=PMX_DEFAULT)
    parser.add_argument('--out-dir', type=Path, default=ASSETS_DIR / 'hd')
    parser.add_argument('--preview', type=Path, default=None, help='Save 6-pose comparison preview image instead of full export.')
    parser.add_argument('--samples', type=int, default=8)
    args = parser.parse_args()

    if not args.pmx.exists():
        print(f'ERROR: PMX not found: {args.pmx}', file=sys.stderr)
        return 1

    app = QGuiApplication(sys.argv)
    t0 = time.time()
    model = PMXModel(args.pmx)
    print(f'Loaded {args.pmx.name} ({len(model.positions)} verts, {len(model.faces)} tris) in {time.time() - t0:.2f}s')

    renderer = GLPMXRenderer(model, width=MASTER_W, height=MASTER_H, samples=args.samples)
    specs = build_state_specs()

    if args.preview is not None:
        picks = [
            ('idle/00', specs['idle'][0]),
            ('walk-right/02', specs['walk-right'][2]),
            ('waving/03', specs['waving'][3]),
            ('sitting/03', specs['sitting'][3]),
            ('sleeping/03', specs['sleeping'][3]),
            ('review/05', specs['review'][5]),
        ]
        tile_w, tile_h = 512, 554
        sheet = Image.new('RGBA', (tile_w * 3, tile_h * 2), (238, 244, 240, 255))
        for idx, (label, spec) in enumerate(picks):
            im = renderer.render(
                spec['rots'],
                spec.get('trans'),
                spec.get('morphs'),
                yaw_deg=spec.get('yaw', 0.0),
                world_y_shift=spec.get('y_shift', 0.0),
            )
            thumb = im.resize((tile_w, tile_h), Image.Resampling.LANCZOS)
            cx = (idx % 3) * tile_w
            cy = (idx // 3) * tile_h
            sheet.alpha_composite(thumb, (cx, cy))
        args.preview.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(args.preview)
        # Also save full-res idle sample alongside preview
        idle_path = args.preview.with_name(args.preview.stem + '_idle.png')
        im0 = renderer.render(specs['idle'][0]['rots'], morph_weights=specs['idle'][0].get('morphs'))
        im0.save(idle_path)
        print(f'Saved preview sheet -> {args.preview} and idle -> {idle_path}')
        return 0

    manifest = json.loads((ASSETS_DIR / 'animations.json').read_text(encoding='utf-8'))
    t1 = time.time()
    count = 0
    for state, entry in manifest.items():
        frames = specs[state]
        files = entry['files']
        if len(frames) != len(files):
            raise RuntimeError(f'Frame count mismatch for {state}: {len(frames)} vs {len(files)}')
        for f_rel, spec in zip(files, frames):
            im = renderer.render(
                spec['rots'],
                spec.get('trans'),
                spec.get('morphs'),
                yaw_deg=spec.get('yaw', 0.0),
                world_y_shift=spec.get('y_shift', 0.0),
            )
            dest = ASSETS_DIR / f_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            im.save(dest, optimize=True)
            count += 1
    print(f'Rendered {count} frames at {MASTER_W}x{MASTER_H} in {time.time() - t1:.2f}s')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
