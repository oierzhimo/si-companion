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
    float texA = texture(uTex, vUV).a;
    // MatKind 1 (肌/sheer gauze sleeve) uses texel.a for gauze density over solid arm skin
    float alpha = (uMatKind == 1) ? uDiffuse.a : (texA * uDiffuse.a);
    if (alpha < 0.65) discard;
    // Normalize view-space depth cleanly across [20.0 .. 90.0] into 16-bit RG
    float d = clamp((vViewPos.z - 20.0) / 70.0, 0.0, 1.0);
    float d_scaled = d * 255.0;
    float hi = floor(d_scaled) / 255.0;
    float lo = fract(d_scaled);
    vec3 vn = normalize(vViewNormal);
    if (!gl_FrontFacing) vn = -vn;
    // Collapse extended face/eye sub-kinds (5..9 -> 0) and Cloth2 (10 -> 2) into 3-bit G-buffer ID [0..4]
    int gKind = uMatKind;
    if (gKind >= 5 && gKind <= 9) gKind = 0;
    else if (gKind == 10) gKind = 2;
    float nzq = floor(clamp(vn.z * 0.5 + 0.5, 0.0, 0.999) * 31.0);
    float b_packed = (float(gKind) * 32.0 + nzq) / 255.0;
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
uniform sampler2D uNormTex;   // Normal map (_N / _HN), or Face SDF on Mat 0
uniform sampler2D uPackTex;   // PBR pack (_P: R=Metallic/Jacquard, B=AO, A=Gloss), or Face Blush (_cm_M) on Mat 0
uniform sampler2D uSpecTex;   // Emissive (_E) on Cloth1/2, Hair Halo (_ST) on Hair, or Face HL (_hl_M) on Mat 0
uniform sampler2D uMatcapTex; // Endfield metallic/horn MatCap (matcap_08_D)
uniform int uHasNorm;
uniform int uHasPack;
uniform int uHasSpec;
uniform int uHasMatcap;
uniform float uNormFlipY;
uniform vec4 uDiffuse;
// 0=face skin, 1=body skin+sheer sleeve, 2=Cloth1, 3=hair, 4=tail mane,
// 5=iris, 6=eye catchlight, 7=sclera, 8=eyelash/brow, 9=inner mouth, 10=Cloth2 (horns/lantern/scales)
uniform int uMatKind;
uniform int uPass;    // 0=opaque cutoff, 1=translucent wisp/lace blend
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
    // On Mat 1 (肌), texel.a encodes sheer ink-wash sleeve gauze density over solid warm arm skin
    float alpha = (uMatKind == 1) ? uDiffuse.a : (texel.a * uDiffuse.a);
    float cutoff = (uMatKind == 3) ? 0.80 : 0.85;
    if (uPass == 0) {
        if (alpha < cutoff) discard;
        alpha = 1.0;
    } else {
        if (uMatKind == 1) discard;
        if (alpha <= 0.025 || alpha >= cutoff) discard;
    }

    vec3 raw = texel.rgb * uDiffuse.rgb;
    float lum = dot(raw, vec3(0.299, 0.587, 0.114));
    float maxC = max(raw.r, max(raw.g, raw.b));
    float minC = min(raw.r, min(raw.g, raw.b));
    float chroma = maxC - minC;

    // PMX uses CW front-facing winding in screen space; gl_FrontFacing is true on geometric backfaces
    vec3 N_raw = normalize(vNormal);
    bool trueBack = gl_FrontFacing;
    vec3 N_trueFront = trueBack ? -N_raw : N_raw;

    vec3 VN_raw = normalize(vViewNormal);
    vec3 VN_front = trueBack ? -VN_raw : VN_raw;
    vec3 V_dir = normalize(-vViewPos); // View-space vector toward camera (camera at origin looking +Z -> V_dir.z < 0? Wait: vViewPos.z > 0, so -vViewPos has z < 0; VN_front on front faces has z < 0!)
    float ndv = clamp(dot(VN_front, V_dir), 0.0, 1.0);
    float rimBase = pow(1.0 - ndv, 3.6);

    vec3 N_pert = N_trueFront;
    vec3 N_shoulder = N_trueFront;
    vec3 N_clothTrue = N_trueFront;
    if (uHasNorm == 1 && uMatKind != 0) {
        vec3 nSamp = texture(uNormTex, vUV).rgb;
        float nx = nSamp.r * 2.0 - 1.0;
        float ny = nSamp.g * 2.0 - 1.0;
        float nz = (nSamp.b < 0.05) ? sqrt(max(0.0, 1.0 - nx * nx - ny * ny)) : (nSamp.b * 2.0 - 1.0);
        float nStrength = (uMatKind == 2 || uMatKind == 10) ? 0.34 : ((uMatKind == 3) ? 0.24 : 0.14);
        N_pert = perturb_normal(N_trueFront, vWorldPos, vUV, vec3(nx, ny, nz), nStrength);
        N_shoulder = perturb_normal(N_trueFront, vWorldPos, vUV, vec3(nx, ny, nz), 0.50);
        N_clothTrue = perturb_normal(N_trueFront, vWorldPos, vUV, vec3(nx, ny, nz), 0.30);
    }

    vec4 packSamp = (uHasPack == 1 && uMatKind != 0) ? texture(uPackTex, vUV) : vec4(0.0, 0.0, 1.0, 0.4);
    float metallic = (uHasPack == 1 && uMatKind != 0) ? packSamp.r : 0.0;
    float bakedAO  = (uHasPack == 1 && uMatKind != 0) ? packSamp.b : 1.0;
    float gloss    = (uHasPack == 1 && uMatKind != 0) ? packSamp.a : 0.35;

    // Key Light (soft front-above-slightly-right, matching Endfield character showcase studio lighting)
    vec3 L = normalize(vec3(-0.14, 0.46, -0.875));
    vec3 H_vec = normalize(L + normalize(vec3(0.0, 0.08, -1.0)));
    float ndl = dot(normalize(mix(N_trueFront, N_pert, 0.40)), L);
    float ndl_dark = dot(normalize(mix(N_trueFront, N_pert, 0.60)), L);
    float ndl_skin = dot(N_trueFront, L);
    float ndl_shoulder = dot(normalize(mix(N_trueFront, N_shoulder, 0.64)), L);
    float ndl_cloth = dot(normalize(mix(N_trueFront, N_clothTrue, 0.42)), L);
    float ndh = max(0.0, dot(normalize(mix(N_trueFront, N_pert, 0.55)), H_vec));

    // MatCap lookup from view-space normal
    vec3 VN_pert = normalize(VN_front + (N_pert - N_trueFront) * 0.45);
    vec2 mcUV = clamp(VN_pert.xy * vec2(0.48, -0.48) + 0.5, 0.02, 0.98);
    vec3 matcapCol = (uHasMatcap == 1) ? texture(uMatcapTex, mcUV).rgb : vec3(0.0);

    float volumeRamp = smoothstep(-0.18, 0.42, ndl);
    float skinRamp = smoothstep(-0.22, 0.72, ndl_skin);

    // Unified Endfield warm porcelain-peach skin lighting palette
    vec3 sharedSkinShadow = vec3(0.855, 0.775, 0.765);
    vec3 sharedSkinLit    = vec3(1.000, 0.982, 0.972);
    vec3 sharedSkinEnv    = mix(sharedSkinShadow, sharedSkinLit, skinRamp);

    // Front-to-back atmospheric layering
    float relZ = vViewPos.z - uPivotZ;
    float rearFactor = smoothstep(0.35, 3.2, relZ);
    float depthAtten = mix(1.0, 0.86, rearFactor);
    float innerShade = trueBack ? 0.83 : 1.0;

    // Soft anatomical contact AO zones
    float underBangs  = smoothstep(18.92, 19.42, vWorldPos.y) * smoothstep(-0.35, -1.12, vWorldPos.z);
    float underChin   = smoothstep(17.15, 17.60, vWorldPos.y) * (1.0 - smoothstep(17.65, 17.90, vWorldPos.y));
    float underJawEdge = smoothstep(-0.34, -0.80, N_trueFront.y) * (1.0 - smoothstep(17.84, 18.00, vWorldPos.y));
    float collarSeam  = smoothstep(16.15, 16.55, vWorldPos.y) * (1.0 - smoothstep(16.55, 17.00, vWorldPos.y));
    float underSkirt  = smoothstep(9.85, 11.45, vWorldPos.y) * (1.0 - smoothstep(11.45, 12.05, vWorldPos.y));

    vec3 outCol;
    if (uMatKind == 0) {
        // Mat 0 (面 — Pure Face Skin): Authentic Endfield symmetric 2D SDF + Cheek Blush + Nose/Lip Highlight!
        vec3 warmPorcelain = pow(clamp(raw, 0.0, 1.0), vec3(0.85, 0.87, 0.89)) * vec3(1.075, 1.058, 1.052) + vec3(0.022, 0.013, 0.012);
        warmPorcelain = clamp(adjust_sat(warmPorcelain, 0.90), 0.0, 0.972);

        // Endfield SDF stores lit distance field on u < 0.5 and cast-shadow threshold on u > 0.5;
        // sample symmetrically from the lit half with slight key-light bias so cheeks & mouth stay porcelain-clean!
        float symU = clamp(0.5 - abs(vUV.x - 0.5) * 0.88 - (vUV.x - 0.5) * 0.06, 0.02, 0.495);
        vec4 sdfSamp = (uHasNorm == 1) ? texture(uNormTex, vec2(symU, vUV.y)) : vec4(0.5, 0.90, 0.85, 1.0);
        vec4 blushSamp = (uHasPack == 1) ? texture(uPackTex, vUV) : vec4(0.0);
        float noseLipHL = (uHasSpec == 1) ? texture(uSpecTex, vUV).r : 0.0;

        vec3 headSmooth = normalize(vec3(N_trueFront.x * 0.72, 0.28, min(N_trueFront.z, -0.45)));
        float faceLight = clamp(dot(headSmooth, L) * 0.58 + smoothstep(0.35, 0.95, sdfSamp.g) * 0.48, 0.0, 1.0);
        float faceRamp = smoothstep(0.20, 0.78, faceLight);
        vec3 faceEnv = mix(vec3(0.895, 0.820, 0.812), vec3(1.004, 0.988, 0.978), faceRamp);

        // Delicate rosy-peach Endfield cheek blush & warm coral lip accent
        float cheekMask = clamp(blushSamp.a * 1.35, 0.0, 1.0);
        float procCheek = exp(-pow((abs(vUV.x - 0.5) - 0.145) / 0.082, 2.0) - pow((vUV.y - 0.515) / 0.062, 2.0));
        float totalBlush = clamp(max(cheekMask * 0.52, procCheek * 0.34), 0.0, 0.52);
        vec3 skinBlushed = mix(warmPorcelain, vec3(0.975, 0.735, 0.725), totalBlush * 0.27);

        // Subtle coral-pink warmth right on the lip line (UV around [0.5, 0.634])
        float lipZone = exp(-pow((vUV.x - 0.5) / 0.048, 2.0) - pow((vUV.y - 0.634) / 0.015, 2.0));
        skinBlushed = mix(skinBlushed, vec3(0.955, 0.695, 0.685), lipZone * 0.24);

        // Crisp anime nose-tip & lower-lip pearl catchlight
        float noseDiamond = exp(-pow((vUV.x - 0.5) / 0.016, 2.0) - pow((vUV.y - 0.536) / 0.018, 2.0));
        float tinyHL = clamp(noseDiamond * 0.42 + noseLipHL * 0.42, 0.0, 0.58);

        faceEnv *= mix(1.0, 0.88, underBangs) * mix(1.0, 0.90, underJawEdge);
        outCol = skinBlushed * faceEnv + vec3(0.052, 0.046, 0.044) * tinyHL;
    } else if (uMatKind == 5) {
        // Mat 1 (目 — Jewel Irises): Deep sapphire upper shadow + luminous cyan-gold radial starburst!
        float eyeTopShadow = smoothstep(0.22, 0.56, vUV.y); // UV y increases downward on iris
        float lowerGlow = smoothstep(0.42, 0.82, vUV.y);
        vec3 vividIris = adjust_sat(pow(clamp(raw, 0.0, 1.0), vec3(0.88)), 1.28);
        vec3 upperShade = mix(vec3(0.58, 0.66, 0.82), vec3(1.04, 1.05, 1.04), eyeTopShadow);
        vec3 starburstBoost = vec3(0.08, 0.13, 0.11) * lowerGlow * smoothstep(0.32, 0.72, lum);
        outCol = vividIris * upperShade + starburstBoost;
    } else if (uMatKind == 6) {
        // Mat 2 (目HL — Sparkling Eye Catchlights): Pure crisp anime eye soul!
        outCol = clamp(raw * 1.35 + vec3(0.22, 0.24, 0.26), 0.0, 1.0);
    } else if (uMatKind == 7) {
        // Mat 3 (目白 — Clean Porcelain Sclera): Cool white with soft upper-lid cel shadow
        float upperLid = smoothstep(18.96, 18.82, vWorldPos.y);
        vec3 scleraLit = vec3(0.962, 0.968, 0.982);
        vec3 scleraShadow = vec3(0.805, 0.835, 0.895);
        outCol = mix(scleraShadow, scleraLit, upperLid);
    } else if (uMatKind == 8) {
        // Mat 5 (睫眉 — Eyelashes, Outer Canthus Vermilion Accent & Eyebrows)
        float isRedAccent = smoothstep(0.08, 0.22, raw.r - raw.g) * smoothstep(0.20, 0.45, raw.r);
        float isBrow = smoothstep(19.04, 19.12, vWorldPos.y);
        vec3 darkLash = clamp(raw * 0.92, 0.06, 0.95);
        vec3 coralCanthus = adjust_sat(clamp(raw * vec3(1.12, 0.96, 0.94), 0.0, 1.0), 1.24);
        // Subtle celadon-charcoal tint on eyebrows so they harmonize softly with hair over bangs
        vec3 softBrow = mix(darkLash, vec3(0.26, 0.33, 0.29), 0.32);
        outCol = mix(mix(darkLash, softBrow, isBrow), coralCanthus, isRedAccent);
    } else if (uMatKind == 9) {
        // Mat 6 (口内 — Inner Mouth / Teeth / Tongue)
        float isTooth = smoothstep(0.62, 0.80, lum) * (1.0 - smoothstep(0.08, 0.20, raw.r - raw.g));
        vec3 softOral = clamp(raw * vec3(1.04, 0.88, 0.88) + vec3(0.06, 0.03, 0.03), 0.0, 0.86);
        outCol = mix(softOral, vec3(0.95, 0.94, 0.93), isTooth);
    } else if (uMatKind == 1) {
        // Mat 7 (肌 — Porcelain Body Skin + Authentic Sheer Ink-Wash Gauze Sleeves!)
        float isWarmSkin = smoothstep(-0.015, 0.055, raw.r - raw.b) * smoothstep(0.35, 0.56, lum);
        vec3 naturalSkin = pow(clamp(raw, 0.0, 1.0), vec3(0.88, 0.90, 0.91)) * vec3(1.045, 1.028, 1.022) + vec3(0.016, 0.008, 0.009);
        naturalSkin = clamp(adjust_sat(naturalSkin, 0.94), 0.0, 0.968);

        // Subtle rosy warmth at knees (y ~ 6.4..7.5) and shoulders (y ~ 16.0..17.0)
        float kneeBlush = exp(-pow((vWorldPos.y - 6.95) / 0.85, 2.0)) * smoothstep(-0.05, -0.65, N_trueFront.z);
        naturalSkin = mix(naturalSkin, vec3(0.965, 0.775, 0.765), kneeBlush * isWarmSkin * 0.18);

        // Authentic Endfield sheer smoky-jade ink-wash sleeve composited over warm arm skin using texel.a!
        float isCuffEmblem = smoothstep(0.76, 0.92, lum) * (1.0 - isWarmSkin);
        float gauzeOpacity = clamp(pow(texel.a, 1.35) * 0.82, 0.18, 0.88);
        vec3 underlyingArmSkin = vec3(0.925, 0.845, 0.825) * sharedSkinEnv;
        vec3 smokyInk = adjust_sat(clamp(raw * vec3(0.82, 0.89, 0.91), 0.0, 1.0), 0.62);
        vec3 sleeveEnv = mix(vec3(0.74, 0.79, 0.82), vec3(1.02, 1.04, 1.04), volumeRamp);
        vec3 litGauze = mix(underlyingArmSkin, smokyInk * sleeveEnv, gauzeOpacity);
        // Crisp silver-pearl diamond emblem on the back of the hand/wrist
        litGauze = mix(litGauze, clamp(raw * 1.12 + vec3(0.06, 0.08, 0.08), 0.0, 0.98), isCuffEmblem);

        float contact = mix(1.0, 0.84, underChin * isWarmSkin)
                      * mix(1.0, 0.87, collarSeam * isWarmSkin)
                      * mix(1.0, 0.83, underSkirt * isWarmSkin);
        float zAtten = mix(depthAtten, 1.0, 0.42 * isWarmSkin);
        vec3 litSkin = naturalSkin * sharedSkinEnv * contact + vec3(0.035, 0.032, 0.030) * rimBase * isWarmSkin * 0.45;
        outCol = mix(litGauze, litSkin, isWarmSkin) * mix(1.0, zAtten, 0.72);
    } else if (uMatKind == 3) {
        // Mat 12 (发 — Cool Misty Sage-Celadon Jade Hair + Anisotropic Angel-Ring Halo + Crown Gold Ornament!)
        float isCrownOrnament = (1.0 - smoothstep(0.05, 0.11, chroma)) * smoothstep(19.25, 19.65, vWorldPos.y) * smoothstep(0.18, 0.48, lum);
        float isDarkStreak = (1.0 - smoothstep(0.23, 0.40, lum)) * (1.0 - smoothstep(0.07, 0.18, chroma)) * (1.0 - isCrownOrnament);

        // Harmonize raw green albedo into Endfield's refined misty sage-celadon jade (richer depth, zero neon lime!)
        vec3 celadonHair = adjust_sat(clamp(raw * vec3(0.945, 0.965, 0.925) + vec3(0.010, 0.014, 0.014), 0.0, 0.92), 0.78);
        // Root-to-tip gentle atmospheric pine-jade gradient on long twin-tails & buns
        float longTailTip = 1.0 - smoothstep(8.5, 16.5, vWorldPos.y);
        celadonHair = mix(celadonHair, celadonHair * vec3(0.82, 0.91, 0.89), longTailTip * 0.48);
        vec3 darkHair = clamp(raw * 0.90 + vec3(0.022, 0.036, 0.034), 0.075, 1.0);
        vec3 crownGold = clamp(raw * vec3(1.42, 1.28, 0.96) + vec3(0.11, 0.09, 0.04) + matcapCol * vec3(0.55, 0.46, 0.26), 0.0, 0.92);

        vec3 base = mix(mix(celadonHair, darkHair, isDarkStreak), crownGold, isCrownOrnament);

        // Endfield painted anisotropic hair halo mask (_ST) + crown tangent specular
        float stMask = (uHasSpec == 1) ? texture(uSpecTex, vUV).r : 0.5;
        float haloBand = smoothstep(0.56, 0.93, stMask) * smoothstep(0.05, 0.68, ndl) * (1.0 - rearFactor * 0.75);
        float crownSheen = smoothstep(0.38, 0.82, N_pert.y) * smoothstep(0.15, 0.68, ndl) * (1.0 - rearFactor);
        float totalHalo = clamp(haloBand * 0.60 + crownSheen * 0.35, 0.0, 1.0) * (1.0 - isDarkStreak) * (1.0 - isCrownOrnament);

        vec3 hairShadow = vec3(0.71, 0.79, 0.765);
        vec3 hairLit    = vec3(1.015, 1.028, 1.005);
        vec3 lightEnv   = mix(hairShadow, hairLit, volumeRamp) * mix(1.0, depthAtten, 0.85);
        float hairAO    = mix(0.80, 1.0, clamp(bakedAO, 0.0, 1.0));

        // Soft warm forehead translucency on bangs tips hovering over skin (y ~ 18.5..19.05, front -Z)
        float bangsTipWarmth = exp(-pow((vWorldPos.y - 18.78) / 0.28, 2.0)) * smoothstep(-0.65, -1.05, vWorldPos.z) * (1.0 - isDarkStreak);
        vec3 litHair = base * lightEnv * hairAO
                     + vec3(0.165, 0.172, 0.140) * totalHalo
                     + vec3(0.050, 0.070, 0.062) * rimBase * (1.0 - rearFactor * 0.5) * 0.50;
        outCol = mix(litHair, mix(litHair, vec3(0.95, 0.87, 0.84), 0.20), bangsTipWarmth);
    } else if (uMatKind == 10) {
        // Mat 9 (Cloth2 — Luminous Amber-Obsidian Dragon Horns, Glowing Jade-Rune Lantern & Iridescent Tail Scales!)
        vec3 emi = (uHasSpec == 1) ? texture(uSpecTex, vUV).rgb : vec3(0.0);
        float isHorn = smoothstep(19.55, 20.05, vWorldPos.y);
        float isWarmAmber = smoothstep(0.08, 0.22, raw.r - raw.b) * smoothstep(0.24, 0.52, raw.g);
        float isDarkObsidian = 1.0 - smoothstep(0.10, 0.25, lum);

        // Glossy obsidian horn tips with cool cyan-silver MatCap ridge highlight
        float hornFacing = smoothstep(-0.10, 0.65, ndl_dark);
        vec3 obsidianCol = mix(vec3(0.045, 0.058, 0.068), vec3(0.18, 0.22, 0.25), hornFacing)
                         + matcapCol * vec3(0.42, 0.54, 0.60) * isHorn * 0.85
                         + vec3(0.10, 0.16, 0.19) * rimBase * isHorn * 0.75;

        // Luminous warm golden-amber horn veins & talisman glow
        vec3 amberGlow = clamp(raw * vec3(1.22, 1.08, 0.78) + emi * vec3(0.75, 0.58, 0.18) + vec3(0.12, 0.08, 0.0) * isHorn, 0.0, 1.0);

        // Iridescent teal-jade dragon tail scales & ancient lantern bronze/jade
        vec3 scaleBase = adjust_sat(raw, 1.12);
        vec3 scaleEnv = mix(vec3(0.74, 0.80, 0.83), vec3(1.06, 1.07, 1.05), volumeRamp) * depthAtten;
        float specHighlight = pow(ndh, 18.0) * gloss * 0.22;
        vec3 regularCloth2 = scaleBase * scaleEnv * mix(0.70, 1.0, bakedAO) + vec3(0.09, 0.13, 0.14) * specHighlight;

        vec3 combined = mix(regularCloth2, obsidianCol, isDarkObsidian);
        combined = mix(combined, amberGlow, isWarmAmber);
        // Add authentic cyan-jade seal rune emission on waist lantern & horn inner glow
        vec3 runeGlow = emi * mix(vec3(0.42, 0.62, 0.56), vec3(0.45, 0.36, 0.10), isWarmAmber);
        outCol = combined + runeGlow * (1.0 - isWarmAmber * 0.45);
    } else if (uMatKind == 4) {
        // Mat 14 (尾鬃 — Dragon Tail Fins / Mane)
        float isDarkMetal = 1.0 - smoothstep(0.12, 0.26, lum);
        float facingDark = smoothstep(-0.08, 0.56, ndl_dark);
        vec3 darkShadow = max(raw * 0.84, vec3(0.045));
        vec3 darkLit = clamp(raw * 1.62 + vec3(0.105), 0.0, 0.32);
        vec3 darkTone = mix(darkShadow, darkLit, facingDark);
        vec3 regularTone = adjust_sat(raw, 1.08);
        vec3 base = mix(regularTone, darkTone, isDarkMetal);
        float metalCrevice = mix(1.0, mix(0.60, 1.0, clamp(bakedAO, 0.0, 1.0)), isDarkMetal);
        vec3 lightEnv = mix(vec3(0.74, 0.80, 0.82), vec3(1.06, 1.07, 1.04), volumeRamp) * depthAtten;
        outCol = base * lightEnv * metalCrevice + vec3(0.06, 0.09, 0.09) * rimBase * 0.45;
    } else {
        // Mat 8 & 10 (Cloth1 — Silk Jacquard Dress, Capes, Shoulder Armor, Champagne-Gold Hardware, Stockings & Tail Astrolabe Glow!)
        vec3 emi = (uHasSpec == 1) ? texture(uSpecTex, vUV).rgb : vec3(0.0);
        float isDarkPiece = 1.0 - smoothstep(0.11, 0.24, lum);
        float inShoulderZone = smoothstep(13.5, 14.3, vWorldPos.y) * max(smoothstep(0.75, 1.35, abs(vWorldPos.x)), smoothstep(15.6, 16.2, vWorldPos.y));
        float inWedgeBand = smoothstep(12.85, 13.30, vWorldPos.y) * (1.0 - smoothstep(15.40, 15.75, vWorldPos.y))
                          * smoothstep(1.18, 1.38, abs(vWorldPos.x)) * (1.0 - smoothstep(0.90, 1.20, vWorldPos.z));
        float inHalterStrap = smoothstep(14.70, 15.05, vWorldPos.y) * (1.0 - smoothstep(16.75, 17.15, vWorldPos.y))
                            * (1.0 - smoothstep(1.38, 1.68, abs(vWorldPos.x)));
        float isSoftSlateTrim = max(inWedgeBand, inHalterStrap);
        float isDarkShoulder = (1.0 - smoothstep(0.36, 0.44, lum)) * (1.0 - smoothstep(0.08, 0.16, chroma)) * inShoulderZone;
        float isDarkWedge = (1.0 - smoothstep(0.36, 0.44, lum)) * (1.0 - smoothstep(0.08, 0.16, chroma)) * isSoftSlateTrim;
        float isDarkUpperOrWedge = max(isDarkShoulder, isDarkWedge);

        float facingDarkOther = smoothstep(-0.08, 0.56, ndl_dark);
        vec3 darkShadowOther = max(raw * 0.84, vec3(0.045));
        vec3 darkLitOther = clamp(raw * 1.48 + vec3(0.085), 0.0, 0.27);
        vec3 darkOther = mix(darkShadowOther, darkLitOther, facingDarkOther) + matcapCol * 0.18 * isDarkPiece;

        float facingShoulder = smoothstep(0.24, 0.88, ndl_shoulder);
        vec3 shoulderShadow = max(raw * 0.46, vec3(0.022));
        vec3 shoulderLit = min(raw * 1.02 + vec3(0.036) + matcapCol * 0.22, vec3(0.31));
        vec3 darkShoulder = mix(shoulderShadow, shoulderLit, facingShoulder);

        vec3 wedgeSlateBase = clamp(raw * 1.40 + vec3(0.165, 0.172, 0.184), 0.0, 0.54);
        vec3 halterSlateBase = clamp(raw * 1.55 + vec3(0.145, 0.150, 0.162), 0.0, 0.52);
        darkShoulder = mix(darkShoulder, mix(wedgeSlateBase, halterSlateBase, inHalterStrap), isDarkWedge);

        float darkBlend = max(isDarkPiece, isDarkUpperOrWedge);
        vec3 base = mix(raw, mix(darkOther, darkShoulder, isDarkUpperOrWedge), darkBlend);
        float isLightCloth = smoothstep(0.55, 0.72, lum) * (1.0 - smoothstep(0.10, 0.22, chroma));
        vec3 softCloth = adjust_sat(clamp(raw * 1.10, 0.0, 0.96), 0.14);
        base = mix(base, softCloth, isLightCloth);
        base = mix(base, adjust_sat(base, 1.22), smoothstep(0.12, 0.26, chroma) * (1.0 - darkBlend));

        // Champagne-gold metallic hardware boost (buckles, hem tabs, shoulder cross plates, tassel beads)
        float isGoldHardware = smoothstep(0.48, 0.78, metallic) * (1.0 - darkBlend * 0.6);
        float goldSpec = pow(ndh, mix(8.0, 28.0, gloss)) * isGoldHardware;
        vec3 goldBoost = raw * vec3(1.18, 1.12, 0.94) + matcapCol * vec3(0.52, 0.45, 0.28) + vec3(0.24, 0.20, 0.11) * goldSpec;
        base = mix(base, clamp(goldBoost, 0.0, 0.98), isGoldHardware * 0.72);

        float clothSeamAO = mix(0.93, 1.0, clamp(bakedAO, 0.0, 1.0)) * mix(1.0, 0.92, collarSeam * 0.5);
        float darkCreviceOther = mix(0.56, 1.0, clamp(bakedAO, 0.0, 1.0)) * mix(1.0, 0.84, collarSeam);
        float tightCrevice = smoothstep(0.05, 0.28, clamp(bakedAO, 0.0, 1.0));
        float darkCreviceShoulder = mix(0.40, 1.0, tightCrevice) * mix(1.0, 0.80, collarSeam * 0.75);
        float tightWedgeSeam = smoothstep(0.01, 0.08, clamp(bakedAO, 0.0, 1.0));
        float darkCreviceWedge = mix(mix(0.62, 0.70, inHalterStrap), 1.0, tightWedgeSeam);
        darkCreviceShoulder = mix(darkCreviceShoulder, darkCreviceWedge, isDarkWedge);
        float darkCreviceAO = mix(darkCreviceOther, darkCreviceShoulder, isDarkUpperOrWedge);
        float finalAO = mix(clothSeamAO, darkCreviceAO, darkBlend);
        float darkInnerOther = mix(innerShade, trueBack ? 0.74 : 1.0, isDarkPiece);
        float darkInnerShoulder = mix(trueBack ? 0.66 : 1.0, trueBack ? 0.88 : 1.0, isDarkWedge);
        float darkInner = mix(darkInnerOther, darkInnerShoulder, isDarkUpperOrWedge);

        // Dress & Side Capes: crisp silver-white silk jacquard sheen on upper bodice + rich celadon mountain ink-wash at hem
        float inDressOrCape = (1.0 - darkBlend) * (1.0 - isGoldHardware * 0.8) * max(smoothstep(7.5, 8.5, vWorldPos.y), smoothstep(1.75, 2.25, abs(vWorldPos.x)));
        float isCapeWing = smoothstep(1.9, 2.5, abs(vWorldPos.x)) * (1.0 - smoothstep(13.2, 14.5, vWorldPos.y));
        float clothRamp = smoothstep(0.08, 0.88, ndl_cloth);
        vec3 baseClothClean = mix(raw, adjust_sat(raw, 1.22), smoothstep(0.10, 0.26, chroma));
        vec3 envDressCape = mix(vec3(0.71, 0.735, 0.775), vec3(0.975, 0.980, 0.985), clothRamp);
        envDressCape *= mix(1.0, 0.93, isCapeWing);
        // Subtle silk jacquard geometric shimmer where metallic channel encodes woven triangles (0.12..0.45)
        float jacquardMask = smoothstep(0.10, 0.28, metallic) * (1.0 - smoothstep(0.45, 0.62, metallic)) * (1.0 - isCapeWing);
        float silkSheen = jacquardMask * pow(max(0.0, ndl_cloth), 2.2) * 0.085;
        float cleanSeamAO = mix(0.92, 1.0, clamp(bakedAO, 0.0, 1.0)) * mix(1.0, 0.90, collarSeam * 0.6);
        float cleanInner = trueBack ? 0.83 : 1.0;
        float cleanDepth = mix(1.0, 0.88, rearFactor);
        vec3 dressCapeCol = baseClothClean * envDressCape * cleanSeamAO * cleanInner * cleanDepth + vec3(silkSheen);

        // Subtle skin warmth through upper sheer patterned stocking cuff (y ~ 4.4..6.0)
        float inUpperStocking = smoothstep(4.2, 5.1, vWorldPos.y) * (1.0 - smoothstep(5.8, 6.3, vWorldPos.y)) * (1.0 - smoothstep(1.6, 2.0, abs(vWorldPos.x)));
        vec3 envOther = mix(vec3(0.76, 0.78, 0.80), vec3(1.05, 1.05, 1.05), volumeRamp);
        vec3 envShoulder = mix(vec3(0.72, 0.74, 0.78), vec3(1.05, 1.05, 1.05), smoothstep(0.05, 0.75, ndl_shoulder));
        vec3 envWedge = mix(vec3(0.78, 0.80, 0.84), vec3(1.02, 1.02, 1.02), smoothstep(-0.20, 0.72, ndl_shoulder));
        envShoulder = mix(envShoulder, envWedge, isDarkWedge);
        vec3 lightEnv = mix(envOther, envShoulder, isDarkUpperOrWedge);
        float wedgeDepth = mix(depthAtten, mix(depthAtten, 1.0, 0.78), isDarkWedge);
        vec3 stdCol = base * lightEnv * finalAO * darkInner * wedgeDepth;
        stdCol = mix(stdCol, mix(stdCol, vec3(0.92, 0.84, 0.82), 0.22), inUpperStocking * (1.0 - darkBlend));

        outCol = mix(stdCol, dressCapeCol, inDressOrCape)
               + emi * 0.88
               + vec3(0.05, 0.065, 0.07) * rimBase * (isDarkPiece * 0.65 + 0.18) * (1.0 - rearFactor * 0.5);
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

        # Texture paths (prefer unstripped Endfield engine diffuse maps from 'other tex' for sheer gauze sleeves & feathered hair tips!)
        tex_cnt = read('<i')
        self.tex_paths = [read_text(enc).replace('\\', '/') for _ in range(tex_cnt)]
        self.tex_resolved: List[Path] = []
        prefer_unstripped = {'T_actor_jsspsi_body_01_D.png', 'T_actor_jsspsi_hair_01_D.png'}
        for rel in self.tex_paths:
            fname = Path(rel).name
            alt = path.parent / 'other tex' / fname
            p = path.parent / rel
            if fname in prefer_unstripped and alt.is_file():
                self.tex_resolved.append(alt)
            elif p.exists():
                self.tex_resolved.append(p)
            else:
                self.tex_resolved.append(alt)

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

        self.matcap_tex = load_aux('T_actor_common_matcap_08_D.png')
        self.mat_aux: Dict[int, Tuple[QOpenGLTexture | None, QOpenGLTexture | None, QOpenGLTexture | None]] = {}
        aux_map = {
            'T_actor_jsspsi_body_01_D.png': ('T_actor_jsspsi_body_01_N.png', None, None),
            'T_actor_jsspsi_cloth_01_D.png': ('T_actor_jsspsi_cloth_01_N.png', 'T_actor_jsspsi_cloth_01_P.png', 'T_actor_jsspsi_cloth_01_E.png'),
            'T_actor_jsspsi_cloth_02_D.png': ('T_actor_jsspsi_cloth_02_N.png', 'T_actor_jsspsi_cloth_02_P.png', 'T_actor_jsspsi_cloth_02_E.png'),
            'T_actor_jsspsi_cloth_04_D.png': ('T_actor_jsspsi_cloth_04_N.png', 'T_actor_jsspsi_cloth_04_P.png', None),
            'T_actor_jsspsi_hair_01_D.png': ('T_actor_jsspsi_hair_01_HN.png', 'T_actor_jsspsi_hair_01_P.png', 'T_actor_jsspsi_hair_01_ST.png'),
        }
        for i, mat in enumerate(model.materials):
            if mat['name'] == '面':
                self.mat_aux[i] = (
                    load_aux('T_actor_common_female_face_01_SDF.png'),
                    load_aux('T_actor_common_female_face_01_cm_M.png'),
                    load_aux('T_actor_common_face_01_hl_M.png'),
                )
                continue
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

        # Dedicated Endfield NPR/PBR material classification per true sub-material
        self.mat_kinds = {}
        for i, mat in enumerate(model.materials):
            name = mat['name']
            if name == '面':
                self.mat_kinds[i] = 0
            elif name == '目':
                self.mat_kinds[i] = 5
            elif name == '目HL':
                self.mat_kinds[i] = 6
            elif name == '目白':
                self.mat_kinds[i] = 7
            elif name == '睫眉':
                self.mat_kinds[i] = 8
            elif name == '口内':
                self.mat_kinds[i] = 9
            elif name == '肌':
                self.mat_kinds[i] = 1
            elif name == '发':
                self.mat_kinds[i] = 3
            elif name == '尾鬃':
                self.mat_kinds[i] = 4
            elif name == 'Cloth2':
                self.mat_kinds[i] = 10
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
        y_view_range: float = 24.0,
        bottom_px: float = BASELINE_BOTTOM_PX,
        anchor_y: float | None = None,
        draw_shadow: bool = True,
    ) -> Image.Image:
        self.ctx.makeCurrent(self.surface)
        skinned, skinned_n = self.model.skin(bone_rots, bone_trans, morph_weights)
        # Auto-ground lowest vertex onto y=0 (unless caller provides explicit bust anchor_y)
        min_y = float(skinned[:, 1].min())
        if anchor_y is None:
            skinned[:, 1] += (-min_y) + world_y_shift
        else:
            skinned[:, 1] += (-float(anchor_y)) + world_y_shift

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
        pivot_y = 9.5 if anchor_y is None else (y_view_range * 0.45)
        pivot = np.array([0.0, pivot_y, 0.0], dtype=np.float32)
        M_pivot[:3, 3] = pivot - R_cam @ pivot

        # View matrix: translate world by (0, -target_y, +cam_dist) so camera is at origin looking along +Z (z_cam > 0)
        V = np.array([
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, -target_y],
            [0.0, 0.0, 1.0, cam_dist],
            [0.0, 0.0, 0.0, 1.0],
        ], dtype=np.float32)

        # Perspective projection for +Z forward: x_ndc = (f/aspect)*x/z, y_ndc = f*y/z, z_ndc = (near+far)/(far-near) + (-2*near*far)/(far-near)/z
        z_near, z_far = 8.0, 110.0
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

        # Projective floor alignment for grounded poses (sitting/sleeping shoes move closer in -Z,
        # which perspective otherwise drops below BASELINE_BOTTOM_PX down to canvas edge y=1664).
        if anchor_y is None and abs(world_y_shift) < 0.05:
            low_band = np.where(skinned[:, 1] < 1.6)[0]
            if len(low_band):
                pts = skinned[low_band]
                hom = np.concatenate([pts, np.ones((len(pts), 1), dtype=np.float32)], axis=1)
                clip = (mvp @ hom.T).T
                ndc_y = clip[:, 1] / np.maximum(clip[:, 3], 1e-4)
                min_idx = int(np.argmin(ndc_y))
                min_ndc_y = float(ndc_y[min_idx])
                if abs(min_ndc_y - bottom_ndc) > 1e-4:
                    dy_world = (bottom_ndc - min_ndc_y) * float(clip[min_idx, 3]) / f
                    skinned[:, 1] += dy_world

        # Update dynamic pos + normal VBOs
        flat_pos = skinned[self.flat_idx].astype(np.float32).tobytes()
        flat_n = skinned_n[self.flat_idx].astype(np.float32).tobytes()
        self.pos_vbo.bind()
        self.pos_vbo.write(0, flat_pos, len(flat_pos))
        self.norm_vbo.bind()
        self.norm_vbo.write(0, flat_n, len(flat_n))
        from PySide6.QtGui import QMatrix4x4, QVector3D, QVector4D
        qm = QMatrix4x4(*mvp.flatten().tolist())
        qmv = QMatrix4x4(*mv.flatten().tolist())

        cam_dir = (R_cam.T @ np.array([0.0, 0.0, -1.0], dtype=np.float32)).tolist()

        gl = self.gl
        loc_tex = self.prog.uniformLocation('uTex')
        loc_norm = self.prog.uniformLocation('uNormTex')
        loc_pack = self.prog.uniformLocation('uPackTex')
        loc_spec = self.prog.uniformLocation('uSpecTex')
        loc_mcap = self.prog.uniformLocation('uMatcapTex')
        loc_has_norm = self.prog.uniformLocation('uHasNorm')
        loc_has_pack = self.prog.uniformLocation('uHasPack')
        loc_has_spec = self.prog.uniformLocation('uHasSpec')
        loc_has_mcap = self.prog.uniformLocation('uHasMatcap')
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
        gl.glUniform1i(loc_mcap, 4)
        if self.matcap_tex is not None:
            self.matcap_tex.bind(4)
            gl.glUniform1i(loc_has_mcap, 1)
        else:
            gl.glUniform1i(loc_has_mcap, 0)
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

            # Lower threshold on hair (mat_id == 3) for bangs/buns separation; keep cloth depth contour subtle & thin
            is_hair = (mat_id == 3) | (m_nb == 3)
            d_thresh = np.where(is_hair, 0.11, 0.42)
            fg_slope = np.where(is_hair, 0.80, 0.35)
            fg_step = np.where(g_alpha & a_nb, np.clip((d_nb - depth - d_thresh) * fg_slope, 0.0, 1.0), 0.0)
            mat_step = np.where(g_alpha & a_nb & is_hair & (mat_id != m_nb), 0.48, 0.0)
            sil_step = np.where(g_alpha & (~a_nb) & ((mat_id == 2) | (mat_id == 3)), 0.38, 0.0)
            edge = np.maximum(edge, np.maximum(np.maximum(fg_step, mat_step), sil_step))

        # 3x3 gentle Gaussian smoothing on G-buffer edge so aliased gbuf pixels never introduce staircases onto 8x MSAA!
        ep = np.pad(edge, ((1, 1), (1, 1)), mode='edge')
        edge_smooth = (
            ep[1:-1, 1:-1] * 0.40
            + (ep[0:-2, 1:-1] + ep[2:, 1:-1] + ep[1:-1, 0:-2] + ep[1:-1, 2:]) * 0.10
            + (ep[0:-2, 0:-2] + ep[0:-2, 2:] + ep[2:, 0:-2] + ep[2:, 2:]) * 0.05
        )
        is_hair_px = (mat_id == 3)[:, :, None]
        is_cloth_px = (mat_id == 2)[:, :, None]
        ink_rgb = np.where(
            is_hair_px,
            np.array([48.0, 68.0, 60.0], dtype=np.float32),
            np.array([44.0, 52.0, 60.0], dtype=np.float32),
        )
        contour_weight = np.where(is_hair_px[:, :, 0], 0.20, np.where(is_cloth_px[:, :, 0], 0.14, 0.0))
        ink_alpha = (edge_smooth * contour_weight)[:, :, None]
        valid = (arr[:, :, 3:4] > 210)
        arr_f = arr[:, :, :3].astype(np.float32)
        arr_f = np.where(valid, arr_f * (1.0 - ink_alpha) + ink_rgb * ink_alpha, arr_f)
        arr[:, :, :3] = np.clip(arr_f, 0, 255).astype(np.uint8)

        # Dual soft sole shadows consistent with Front-Slightly-Above key light (centered, softly trailing slightly back)
        if draw_shadow and anchor_y is None and abs(world_y_shift) < 0.05:
            y0_scan = max(0, int(bottom_px) - 24)
            y1_scan = min(self.height, int(bottom_px) + 4)
            mid_x = self.width // 2
            for x_lo, x_hi in ((max(0, mid_x - 260), mid_x), (mid_x, min(self.width, mid_x + 260))):
                band = arr[y0_scan:y1_scan, x_lo:x_hi, 3] > 95
                xs_foot = np.where(np.any(band, axis=0))[0] + x_lo
                if len(xs_foot) < 6:
                    continue
                cx_f = float((xs_foot.min() + xs_foot.max()) * 0.5)
                rx = max(52.0, min(100.0, float(xs_foot.max() - xs_foot.min()) * 0.64))
                ry = 10.0
                sy_c = float(bottom_px) + 2.0
                sx0 = max(0, int(cx_f - rx * 1.75))
                sx1 = min(self.width, int(cx_f + rx * 1.75))
                sy0 = max(0, int(sy_c - ry * 1.8))
                sy1 = min(self.height, int(sy_c + ry * 2.0))
                if sx1 <= sx0 or sy1 <= sy0:
                    continue
                yy, xx = np.mgrid[sy0:sy1, sx0:sx1]
                d2 = ((xx - cx_f) / rx) ** 2 + ((yy - sy_c) / ry) ** 2
                sh_a = (np.exp(-d2 * 1.8) * 0.18 + np.exp(-d2 * 4.5) * 0.10).astype(np.float32)
                sh_a = np.where(d2 < 3.0, sh_a, 0.0).astype(np.float32)
                fg_r = arr[sy0:sy1, sx0:sx1, :3].astype(np.float32) / 255.0
                fg_a = arr[sy0:sy1, sx0:sx1, 3:4].astype(np.float32) / 255.0
                sh_rgb = np.array([72.0 / 255.0, 78.0 / 255.0, 86.0 / 255.0], dtype=np.float32)
                out_a = fg_a[:, :, 0] + sh_a * (1.0 - fg_a[:, :, 0])
                out_rgb = (
                    fg_r * fg_a + sh_rgb * (sh_a[:, :, None] * (1.0 - fg_a))
                ) / np.maximum(out_a[:, :, None], 1e-6)
                arr[sy0:sy1, sx0:sx1, :3] = np.clip(out_rgb * 255.0, 0, 255).astype(np.uint8)
                arr[sy0:sy1, sx0:sx1, 3] = np.clip(out_a * 255.0, 0, 255).astype(np.uint8)

        return Image.fromarray(arr, 'RGBA')


# ---- Natural Idle, Anatomical Hand Articulation & Chain Draping Helpers ----

def hand_pose(side: str, preset: str = 'relaxed', scale: float = 1.0) -> Dict[str, Tuple[float, float, float]]:
    """Return anatomical 15-joint finger curl angles for Left ('L', +X) or Right ('R', -X) hand.

    In 祀.pmx's A-pose rest skeleton, curling into the palm corresponds to negative Z rotation
    on the Left hand and positive Z rotation on the Right hand.
    """
    jp = '左' if side == 'L' else '右'
    s = (-1.0 if side == 'L' else 1.0) * scale
    sy = (1.0 if side == 'L' else -1.0) * scale
    r: Dict[str, Tuple[float, float, float]] = {}

    if preset == 'relaxed':
        # Natural graceful anime cascade from index (softest) to pinky (deepest)
        curls = {
            '人指': (13.0, 17.0, 11.0),
            '中指': (19.0, 24.0, 15.0),
            '薬指': (25.0, 30.0, 19.0),
            '小指': (31.0, 35.0, 23.0),
        }
        for fname, (c1, c2, c3) in curls.items():
            r[f'{jp}{fname}１'] = (0.0, 0.0, s * c1)
            r[f'{jp}{fname}２'] = (0.0, 0.0, s * c2)
            r[f'{jp}{fname}３'] = (0.0, 0.0, s * c3)
        r[f'{jp}親指０'] = (8.0 * scale, sy * 10.0, s * 10.0)
        r[f'{jp}親指１'] = (6.0 * scale, sy * 8.0, s * 12.0)
        r[f'{jp}親指２'] = (0.0, sy * 5.0, s * 10.0)
    elif preset == 'wave_open':
        # Softly fanned feminine open palm facing forward
        curls = {
            '人指': (4.0, 6.0, 4.0),
            '中指': (6.0, 8.0, 5.0),
            '薬指': (9.0, 12.0, 8.0),
            '小指': (13.0, 16.0, 10.0),
        }
        splay = {'人指': -4.0, '中指': -1.0, '薬指': 2.5, '小指': 6.0}
        for fname, (c1, c2, c3) in curls.items():
            sp = splay[fname] * sy
            r[f'{jp}{fname}１'] = (0.0, sp, s * c1)
            r[f'{jp}{fname}２'] = (0.0, 0.0, s * c2)
            r[f'{jp}{fname}３'] = (0.0, 0.0, s * c3)
        r[f'{jp}親指０'] = (6.0 * scale, sy * 8.0, s * 6.0)
        r[f'{jp}親指１'] = (4.0 * scale, sy * 6.0, s * 8.0)
        r[f'{jp}親指２'] = (0.0, sy * 4.0, s * 6.0)
    elif preset == 'chin_thought':
        # Slender index extended softly toward chin; middle/ring/pinky neatly tucked into palm
        r[f'{jp}人指１'] = (0.0, 0.0, s * 8.0)
        r[f'{jp}人指２'] = (0.0, 0.0, s * 14.0)
        r[f'{jp}人指３'] = (0.0, 0.0, s * 10.0)
        for fname, (c1, c2, c3) in {
            '中指': (68.0, 78.0, 55.0),
            '薬指': (74.0, 82.0, 60.0),
            '小指': (78.0, 84.0, 62.0),
        }.items():
            r[f'{jp}{fname}１'] = (0.0, 0.0, s * c1)
            r[f'{jp}{fname}２'] = (0.0, 0.0, s * c2)
            r[f'{jp}{fname}３'] = (0.0, 0.0, s * c3)
        r[f'{jp}親指０'] = (16.0 * scale, sy * 20.0, s * 24.0)
        r[f'{jp}親指１'] = (12.0 * scale, sy * 16.0, s * 28.0)
        r[f'{jp}親指２'] = (0.0, sy * 10.0, s * 20.0)
    elif preset == 'aha_point':
        # Crisp upward-pointing index finger + cheerful curled fist
        r[f'{jp}人指１'] = (0.0, 0.0, s * 4.0)
        r[f'{jp}人指２'] = (0.0, 0.0, s * 6.0)
        r[f'{jp}人指３'] = (0.0, 0.0, s * 4.0)
        for fname, (c1, c2, c3) in {
            '中指': (70.0, 80.0, 58.0),
            '薬指': (75.0, 82.0, 60.0),
            '小指': (78.0, 84.0, 62.0),
        }.items():
            r[f'{jp}{fname}１'] = (0.0, 0.0, s * c1)
            r[f'{jp}{fname}２'] = (0.0, 0.0, s * c2)
            r[f'{jp}{fname}３'] = (0.0, 0.0, s * c3)
        r[f'{jp}親指０'] = (16.0 * scale, sy * 20.0, s * 24.0)
        r[f'{jp}親指１'] = (12.0 * scale, sy * 16.0, s * 28.0)
        r[f'{jp}親指２'] = (0.0, sy * 10.0, s * 20.0)
    elif preset == 'lap_rest':
        curls = {
            '人指': (18.0, 24.0, 16.0),
            '中指': (22.0, 28.0, 18.0),
            '薬指': (26.0, 32.0, 20.0),
            '小指': (30.0, 35.0, 22.0),
        }
        for fname, (c1, c2, c3) in curls.items():
            r[f'{jp}{fname}１'] = (0.0, 0.0, s * c1)
            r[f'{jp}{fname}２'] = (0.0, 0.0, s * c2)
            r[f'{jp}{fname}３'] = (0.0, 0.0, s * c3)
        r[f'{jp}親指０'] = (10.0 * scale, sy * 12.0, s * 14.0)
        r[f'{jp}親指１'] = (8.0 * scale, sy * 10.0, s * 14.0)
        r[f'{jp}親指２'] = (0.0, sy * 6.0, s * 10.0)
    elif preset == 'flustered':
        curls = {
            '人指': (24.0, 32.0, 20.0),
            '中指': (28.0, 36.0, 24.0),
            '薬指': (34.0, 42.0, 28.0),
            '小指': (38.0, 46.0, 30.0),
        }
        for fname, (c1, c2, c3) in curls.items():
            r[f'{jp}{fname}１'] = (0.0, 0.0, s * c1)
            r[f'{jp}{fname}２'] = (0.0, 0.0, s * c2)
            r[f'{jp}{fname}３'] = (0.0, 0.0, s * c3)
        r[f'{jp}親指０'] = (12.0 * scale, sy * 14.0, s * 16.0)
        r[f'{jp}親指１'] = (10.0 * scale, sy * 12.0, s * 18.0)
    return r


def base_drape() -> Dict[str, Tuple[float, float, float]]:
    """Default natural Endfield stance matching authentic in-game silhouette & finger poise."""
    rots: Dict[str, Tuple[float, float, float]] = {}
    # Gentle relaxed A-stance matching game idle (-35° rest -> ~ -61° natural arm hang)
    rots['左肩'] = (0.0, 0.0, -3.0)
    rots['右肩'] = (0.0, 0.0, 3.0)
    rots['左腕'] = (4.0, 0.0, -26.0)
    rots['右腕'] = (4.0, 0.0, 26.0)
    rots['左ひじ'] = (14.0, 0.0, -7.0)
    rots['右ひじ'] = (14.0, 0.0, 7.0)
    rots['左手首'] = (0.0, 0.0, 5.0)
    rots['右手首'] = (0.0, 0.0, -5.0)

    # Natural relaxed finger cascade on both hands (eliminates flat paddle hands!)
    rots.update(hand_pose('L', 'relaxed'))
    rots.update(hand_pose('R', 'relaxed'))

    # Graceful S-curve drape for wide cape sleeves (hugging leg line softly)
    rots['L_sleeveD_01_jnt_1'] = (5.0, 0.0, 14.5)
    rots['L_sleeveD_01_jnt_2'] = (1.5, 0.0, 4.0)
    rots['L_sleeveD_01_jnt_8'] = (-1.5, 0.0, -1.5)
    rots['L_sleeveD_01_jnt_16'] = (-1.0, 0.0, 1.0)
    rots['R_sleeveD_01_jnt_1'] = (5.0, 0.0, -14.5)
    rots['R_sleeveD_01_jnt_2'] = (1.5, 0.0, -4.0)
    rots['R_sleeveD_01_jnt_8'] = (-1.5, 0.0, 1.5)
    rots['R_sleeveD_01_jnt_16'] = (-1.0, 0.0, -1.0)

    # Yellow shoulder ribbon & sword-tassel drape smoothly along outer sleeve edge
    rots['ribbonB_01_jnt_1'] = (2.0, -8.0, -10.0)
    rots['ribbonB_01_jnt_2'] = (0.0, -4.0, -5.0)
    rots['ribbonB_01_jnt_3'] = (0.0, 0.0, -3.0)
    rots['ribbonB_01_jnt_6'] = (0.0, 0.0, 5.0)
    for k in range(1, 5):
        rots[f'ribbonF_01_jnt_{k}'] = (1.5, -5.0, -2.5)

    # Right arm back ribbon (ribbonC): tuck gently downward
    rots['ribbonC_01_jnt_1'] = (3.0, 0.0, -14.0)
    rots['ribbonC_01_jnt_2'] = (2.0, 0.0, -8.0)

    # Back twin long hair: PMX bind pose juts backward (+Z=6.94) & outward (±X=5.45) for MMD physics init.
    # Positive rx (+3.6 deg) + gentle inward rz brings +Z downward under gravity in a natural S-curve!
    for k in range(1, 12):
        inward = -2.2 if k <= 5 else (-0.8 if k <= 9 else 0.6)
        rots[f'L_hairE_jnt{k}'] = (3.5, -0.8, inward)
        rots[f'R_hairE_jnt{k}'] = (3.5, 0.8, -inward)
    for k in range(12, 17):
        rots[f'L_hairE_jnt{k}'] = (-1.4, 0.4, 1.4)
        rots[f'R_hairE_jnt{k}'] = (-1.4, -0.4, -1.4)

    # Curve jade dragon tail gracefully behind her left ankle (+X, screen right) matching in-game stance
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


def stride_skirt_drape(l_hip_deg: float, r_hip_deg: float) -> Dict[str, Tuple[float, float, float]]:
    """Dynamic per-leg skirt & front-ribbon lift during walking/running strides so thighs never slice through the dress."""
    r: Dict[str, Tuple[float, float, float]] = {}
    lf = max(0.0, l_hip_deg) / 34.0  # Left leg (+X) forward stride [0..1.1]
    rf = max(0.0, r_hip_deg) / 34.0  # Right leg (-X) forward stride [0..1.1]
    lb = max(0.0, -l_hip_deg) / 34.0 # Left leg backward extension
    rb = max(0.0, -r_hip_deg) / 34.0 # Right leg backward extension
    cf = max(lf, rf) * 0.85

    tier_lift = {0: 0.05, 1: 0.14, 2: 0.24, 3: 0.26, 4: 0.18, 5: 0.08, 6: -0.04, 7: -0.08, 8: -0.08}
    # Front columns: 0=center, 1..4=left(+X), 15..12=right(-X)
    front_cols = {
        0:  (cf, 52.0, 0),
        1:  (max(lf, cf * 0.65), 68.0,  1),
        2:  (lf, 76.0,  1),
        3:  (lf * 0.85, 64.0,  1),
        4:  (lf * 0.45, 36.0,  1),
        15: (max(rf, cf * 0.65), 68.0, -1),
        14: (rf, 76.0, -1),
        13: (rf * 0.85, 64.0, -1),
        12: (rf * 0.45, 36.0, -1),
    }
    for col, (amt, deg, side) in front_cols.items():
        if amt <= 0.01:
            continue
        for tier, tf in tier_lift.items():
            rx = deg * tf * amt
            rz = side * 6.0 * max(0.0, tf) * amt
            r[f'裙子_{tier}_{col}'] = (rx, 0.0, rz)

    # Back columns (6..10) trail backward (-rx) when rear leg pushes back + gentle wind flow
    back_cols = {
        6: (lb * 0.7 + 0.12, -52.0, 1),
        7: (lb + 0.15, -62.0, 1),
        8: (max(lb, rb) * 0.85 + 0.15, -58.0, 0),
        9: (rb + 0.15, -62.0, -1),
        10: (rb * 0.7 + 0.12, -52.0, -1),
    }
    for col, (amt, deg, side) in back_cols.items():
        for tier in range(0, 7):
            tf = {0: 0.04, 1: 0.12, 2: 0.20, 3: 0.22, 4: 0.16, 5: 0.10, 6: 0.06}[tier]
            r[f'裙子_{tier}_{col}'] = (deg * tf * amt, 0.0, side * 4.0 * tf * amt)

    for side_prefix, fwd, bwd, side in (('L', lf, lb, 1), ('R', rf, rb, -1)):
        if fwd > 0.01:
            r[f'{side_prefix}_skirtA_01_jnt'] = (14.0 * fwd, 0.0, side * 2.5 * fwd)
            r[f'{side_prefix}_skirtA_02_jnt'] = (28.0 * fwd, 0.0, side * 4.0 * fwd)
            r[f'{side_prefix}_skirtA_03_jnt'] = (14.0 * fwd, 0.0, side * 2.0 * fwd)
            r[f'{side_prefix}_skirtA_04_jnt'] = (-14.0 * fwd, 0.0, 0.0)
            for k, rp in {1: 4.0, 2: 9.0, 3: 14.0, 4: 16.0, 5: 12.0, 6: 6.0, 7: -4.0, 8: -6.0}.items():
                r[f'{side_prefix}_ribbonA_01_jnt_{k}'] = (rp * fwd, 0.0, side * 2.0 * fwd)
        if bwd > 0.01:
            r[f'{side_prefix}_skirtC_01_jnt'] = (-14.0 * bwd, 0.0, 0.0)
            r[f'{side_prefix}_skirtC_02_jnt'] = (-22.0 * bwd, 0.0, 0.0)
    return r


def seated_skirt_drape(p: float, pitch_scale: float = 1.0) -> Dict[str, Tuple[float, float, float]]:
    """Compute progressive skirt, flap, and front-ribbon rotations for seated/curled poses."""
    r: Dict[str, Tuple[float, float, float]] = {}
    s = p * pitch_scale

    # Upper tiers lift forward over thigh top; lower tiers (7-10) curve downward over the lap/knees
    tier_pitch = {
        0: 0.04,
        1: 0.12,
        2: 0.26,
        3: 0.29,
        4: 0.22,
        5: 0.12,
        6: -0.04,
        7: -0.14,
        8: -0.19,
        9: -0.18,
        10: -0.12,
    }
    tier_flare = {
        2: 0.22,
        3: 0.30,
        4: 0.22,
        5: 0.14,
    }
    col_cfg = {
        0:  (50.0, 66.0,  0.0,  0),
        1:  (74.0, 52.0,  3.5,  1),
        15: (74.0, 52.0,  3.5, -1),
        2:  (82.0, 48.0,  7.0,  1),
        14: (82.0, 48.0,  7.0, -1),
        3:  (72.0, 38.0, 11.0,  1),
        13: (72.0, 38.0, 11.0, -1),
        4:  (44.0, 22.0, 13.0,  1),
        12: (44.0, 22.0, 13.0, -1),
        5:  (18.0,  8.0,  6.0,  1),
        11: (18.0,  8.0,  6.0, -1),
    }

    for col, (lift_deg, drape_deg, max_flare, side) in col_cfg.items():
        for tier, pf in tier_pitch.items():
            ff = tier_flare.get(tier, 0.0)
            rx = (lift_deg * pf * s) if pf >= 0 else (drape_deg * pf * s)
            rz = side * max_flare * ff * p
            ry = -side * (max_flare * 0.35) * ff * p if pf > 0 else 0.0
            r[f'裙子_{tier}_{col}'] = (rx, ry, rz)

    for side_prefix, side in (('L', 1), ('R', -1)):
        r[f'{side_prefix}_skirtA_01_jnt'] = (17.0 * s, 0.0, side * 2.5 * p)
        r[f'{side_prefix}_skirtA_02_jnt'] = (35.0 * s, -side * 2.5 * p, side * 4.0 * p)
        r[f'{side_prefix}_skirtA_03_jnt'] = (21.0 * s, 0.0, side * 2.0 * p)
        r[f'{side_prefix}_skirtA_04_jnt'] = (-24.0 * s, 0.0, 0.0)

        r[f'{side_prefix}_skirtB_01_jnt'] = (9.0 * s, 0.0, side * 5.0 * p)
        r[f'{side_prefix}_skirtB_02_jnt'] = (22.0 * s, 0.0, side * 7.0 * p)
        r[f'{side_prefix}_skirtB_03_jnt'] = (10.0 * s, 0.0, side * 3.5 * p)

        rib_pitch = {
            1: 3.5, 2: 8.5, 3: 14.5, 4: 18.5, 5: 17.5,
            6: 10.5, 7: 4.0, 8: -5.0, 9: -9.0, 10: -10.0,
            11: -9.0, 12: -7.0, 13: -4.0,
        }
        rib_flare = {3: 2.0, 4: 3.0, 5: 2.5, 6: 1.2}
        for k, rp in rib_pitch.items():
            rf = rib_flare.get(k, 0.0)
            r[f'{side_prefix}_ribbonA_01_jnt_{k}'] = (rp * s, -side * rf * 0.4 * p, side * rf * p)

    return r


def build_state_specs() -> Dict[str, List[dict]]:
    """Return frame pose definitions for all 14 states in assets/animations.json."""
    base = base_drape()
    specs: Dict[str, List[dict]] = {}

    # 1. idle (6 frames): 0=natural contrapposto stand, 1-3=smooth blink, 4-5=gentle chest breathe & tail S-wave
    idle_frames = []
    blink_map = [0.0, 0.55, 1.0, 0.38, 0.0, 0.0]
    for i in range(6):
        ph = i / 6.0 * 2.0 * math.pi
        breathe = math.sin(ph)
        tail_wave = math.sin(ph - 0.6) * 4.5
        r = merge_rots(base, {
            '腰': (0.0, 1.2, 0.6),
            '上半身': (-0.6 + breathe * 0.9, -1.2, -0.5),
            '上半身2': (breathe * 0.7, -0.6, 0.0),
            '左肩': (0.0, 0.0, -3.0 - breathe * 0.6),
            '右肩': (0.0, 0.0, 3.0 + breathe * 0.6),
            '首': (-0.8, 0.5, 0.4),
            '頭': (-2.2 + breathe * 0.6, -1.8, -1.2),
            '左足': (-1.0, 1.5, 0.8),
            '右足': (1.2, -2.0, -0.8),
            '右ひざ': (-2.5, 0.0, 0.0),
            '右足首': (1.3, 0.0, 0.0),
            'tail_jnt6': (4.0, 14.0 + tail_wave * 0.6, 0.0),
            'tail_jnt8': (2.0, 10.0 + tail_wave, 0.0),
            'tail_jnt12': (-2.0, -4.0 - tail_wave * 0.7, 0.0),
        })
        m = {'口角上げ': 0.16}
        if blink_map[i] > 0:
            m['まばたき'] = blink_map[i]
        idle_frames.append({'rots': r, 'morphs': m, 'yaw': 2.0})
    specs['idle'] = idle_frames

    # 2. walk-right (8 frames) & walk-left (8 frames): fluid contrapposto gait + anti-clipping skirt flow + hair/cape wave
    for side, yaw in (('walk-right', -50.0), ('walk-left', 50.0)):
        frames = []
        for i in range(8):
            t = i / 8.0 * 2.0 * math.pi
            s = math.sin(t)
            c = math.cos(t)
            s_lag = math.sin(t - 0.65)
            l_hip = s * 22.0
            r_hip = -s * 22.0
            l_knee = max(0.0, -c) * 26.0 + 4.0
            r_knee = max(0.0, c) * 26.0 + 4.0
            w_ov = {
                '腰': (0.0, s * 3.5, c * 1.6),
                '左足': (l_hip, 0.0, 1.2),
                '右足': (r_hip, 0.0, -1.2),
                '左ひざ': (-l_knee, 0.0, 0.0),
                '右ひざ': (-r_knee, 0.0, 0.0),
                '左足首': (l_knee * 0.45 - l_hip * 0.35, 0.0, 0.0),
                '右足首': (r_knee * 0.45 - r_hip * 0.35, 0.0, 0.0),
                '上半身': (-2.5, -s * 4.5, -c * 1.2),
                '上半身2': (-1.0, -s * 2.5, 0.0),
                '頭': (1.8 + abs(c) * 0.8, -yaw * 0.22 + s * 2.0, -yaw * 0.03),
                '左腕': (-s * 17.0 + 3.0, 0.0, -29.0),
                '右腕': (s * 17.0 + 3.0, 0.0, 29.0),
                '左ひじ': (16.0 + max(0.0, -s) * 8.0, 0.0, -6.0),
                '右ひじ': (16.0 + max(0.0, s) * 8.0, 0.0, 6.0),
                # Gentle aerodynamic trail on cape sleeves & twin hair
                'L_sleeveD_01_jnt_1': (-5.0 + s_lag * 4.0, 0.0, 15.0),
                'L_sleeveD_01_jnt_6': (-4.0 - s_lag * 3.0, 0.0, 0.0),
                'R_sleeveD_01_jnt_1': (-5.0 - s_lag * 4.0, 0.0, -15.0),
                'R_sleeveD_01_jnt_6': (-4.0 + s_lag * 3.0, 0.0, 0.0),
                'L_hairE_jnt2': (2.5 - abs(s) * 0.8, -0.8, -2.0 + s_lag * 1.8),
                'R_hairE_jnt2': (2.5 - abs(s) * 0.8, 0.8, 2.0 + s_lag * 1.8),
                'tail_jnt4': (5.0, -6.0 + s_lag * 5.0, 0.0),
                'tail_jnt8': (3.0, -8.0 - s_lag * 7.0, 0.0),
            }
            w_ov.update(stride_skirt_drape(l_hip, r_hip))
            r = merge_rots(base, w_ov)
            frames.append({'rots': r, 'morphs': {'口角上げ': 0.20}, 'yaw': yaw})
        specs[side] = frames

    # 3. running-right (8 frames) & running-left (8 frames): energetic dash with zero thigh-dress clipping & trailing capes!
    for side, yaw in (('running-right', -56.0), ('running-left', 56.0)):
        frames = []
        for i in range(8):
            t = i / 8.0 * 2.0 * math.pi
            s = math.sin(t)
            c = math.cos(t)
            s_lag = math.sin(t - 0.75)
            l_hip = s * 33.0
            r_hip = -s * 33.0
            l_knee = max(0.0, -c) * 44.0 + 8.0
            r_knee = max(0.0, c) * 44.0 + 8.0
            run_ov = {
                '腰': (2.0, s * 5.0, c * 2.0),
                '左足': (l_hip, 0.0, 1.5),
                '右足': (r_hip, 0.0, -1.5),
                '左ひざ': (-l_knee, 0.0, 0.0),
                '右ひざ': (-r_knee, 0.0, 0.0),
                '左足首': (l_knee * 0.35 - l_hip * 0.25, 0.0, 0.0),
                '右足首': (r_knee * 0.35 - r_hip * 0.25, 0.0, 0.0),
                '上半身': (-9.0, -s * 6.5, -c * 1.8),
                '上半身2': (-3.0, -s * 3.0, 0.0),
                '頭': (8.5, -yaw * 0.24 + s * 2.5, -yaw * 0.04),
                '左腕': (-s * 28.0 + 6.0, 0.0, -28.0),
                '右腕': (s * 28.0 + 6.0, 0.0, 28.0),
                '左ひじ': (34.0 + max(0.0, -s) * 14.0, 0.0, -12.0),
                '右ひじ': (34.0 + max(0.0, s) * 14.0, 0.0, 12.0),
                # Stream cape wings & tail backward so they never clip rear heel or look static
                'L_sleeveD_01_jnt_1': (-14.0 + s_lag * 5.0, 0.0, 18.0),
                'L_sleeveD_01_jnt_5': (-8.0 - s_lag * 4.0, 0.0, 2.0),
                'R_sleeveD_01_jnt_1': (-14.0 - s_lag * 5.0, 0.0, -18.0),
                'R_sleeveD_01_jnt_5': (-8.0 + s_lag * 4.0, 0.0, -2.0),
                'L_hairE_jnt1': (1.5, -1.0, -3.0),
                'R_hairE_jnt1': (1.5, 1.0, 3.0),
                'L_hairE_jnt4': (1.5 + s_lag * 2.5, 0.0, -1.5),
                'R_hairE_jnt4': (1.5 - s_lag * 2.5, 0.0, 1.5),
                # Lift dragon tail slightly behind waist so rear boot heel clears the astrolabe ring cleanly
                'tail_jnt2': (-8.0, -4.0, 0.0),
                'tail_jnt4': (-6.0, -6.0 + s_lag * 7.0, 0.0),
                'tail_jnt8': (4.0, -4.0 - s_lag * 8.0, 0.0),
            }
            run_ov.update(stride_skirt_drape(l_hip, r_hip))
            r = merge_rots(base, run_ov)
            frames.append({'rots': r, 'morphs': {'にこり': 0.20, '口角上げ': 0.42}, 'yaw': yaw})
        specs[side] = frames

    # 4. waving (4 frames): true elbow-folded, palm-forward friendly wave beside cheek/shoulder!
    # (elbow_rz, wrist_rz, wrist_rx, head_roll, smile_amt)
    wave_frames = [
        (-99.0,  -8.0, -4.0, -3.0, 0.24),
        (-104.0, 15.0, -6.0, -5.2, 0.34),
        (-100.0, -11.0, -4.5, -3.8, 0.28),
        (-103.0,  9.0, -5.5, -4.6, 0.32),
    ]
    specs['waving'] = []
    for elbow_rz, wrist_rz, wrist_rx, head_roll, sm in wave_frames:
        w_ov = {
            '腰': (0.0, -2.0, -1.0),
            '上半身': (-1.5, 3.5, 1.5),
            '頭': (-2.5, -4.0, head_roll),
            '右肩': (0.0, 0.0, -4.0),
            '右腕': (8.0, -10.0, 6.0),
            '右腕捩': (-28.0, -18.0, 0.0),
            '右ひじ': (0.0, -14.0, elbow_rz),
            '右手捩': (62.0, 44.0, 0.0),
            '右手首': (wrist_rx, 0.0, wrist_rz),
            '左腕': (5.0, 0.0, -27.0),
            # Counter-rotate R_sleeveD & ribbonC against upper-arm twist so the right cape wing hangs cleanly downward beside her leg!
            'R_sleeveD_01_jnt_1': (29.0, 23.0, 30.0),
            'R_sleeveD_01_jnt_2': (0.0, 0.0, -6.0),
            'ribbonC_01_jnt_1': (22.0, 18.0, 22.0),
            'tail_jnt8': (2.0, 12.0 + wrist_rz * 0.25, 0.0),
        }
        w_ov.update(hand_pose('R', 'wave_open'))
        r = merge_rots(base, w_ov)
        specs['waving'].append({'rots': r, 'morphs': {'笑い': sm, 'にこり': 0.30, 'にっこり': 0.42, '口角上げ': 0.24}, 'yaw': 3.0})

    # 5. sitting (4 frames, settles on frame 3): dainty knees-together feminine sit, twist-free lap hands, soft curved cape drape
    sit_prog = [0.28, 0.58, 0.86, 1.0]
    specs['sitting'] = []
    for p in sit_prog:
        sit_ov = {
            '腰': (-3.5 * p, 0.0, 0.0),
            '上半身': (-2.8 * p, -4.5 * p, 1.0 * p),
            '上半身2': (-1.5 * p, -2.0 * p, 0.5 * p),
            '頭': (2.5 * p, 5.0 * p, -3.8 * p),
            # Positive ry + negative rz on Left (mirrored on Right) brings knees gracefully together!
            '左足': (75.0 * p, 4.2 * p, -2.2 * p),
            '右足': (75.0 * p, -4.2 * p, 2.2 * p),
            '左ひざ': (-85.0 * p, 0.0, 2.4 * p),
            '右ひざ': (-85.0 * p, 0.0, -2.4 * p),
            '左足首': (-8.0 * p, 0.0, -1.5 * p),
            '右足首': (-8.0 * p, 0.0, 1.5 * p),
            # Twist-free IK lap hands resting softly on thighs
            '左腕': (-0.4 * p + 4.0 * (1 - p), 8.6 * p, -27.4 * p - 26.0 * (1 - p)),
            '右腕': (-0.4 * p + 4.0 * (1 - p), -8.6 * p, 27.4 * p + 26.0 * (1 - p)),
            '左ひじ': (53.2 * p + 14.0 * (1 - p), 30.6 * p, -30.2 * p - 7.0 * (1 - p)),
            '右ひじ': (53.2 * p + 14.0 * (1 - p), -30.6 * p, 30.2 * p + 7.0 * (1 - p)),
            '左手捩': (-18.8 * p, 13.1 * p, 0.0),
            '右手捩': (-18.8 * p, -13.1 * p, 0.0),
            '左手首': (-5.0 * p, 19.7 * p, -12.0 * p),
            '右手首': (-5.0 * p, -19.7 * p, 12.0 * p),
            # Soft S-curved cape drape beside seat (avoids stiff straight tent boards)
            'L_sleeveD_01_jnt_1': (-18.0 * p + 5.0 * (1 - p), 0.0, 20.0 * p + 14.5 * (1 - p)),
            'L_sleeveD_01_jnt_4': (-8.0 * p, 0.0, -3.5 * p),
            'L_sleeveD_01_jnt_9': (-6.0 * p, 0.0, -4.0 * p),
            'L_sleeveD_01_jnt_15': (5.0 * p, 0.0, 3.0 * p),
            'R_sleeveD_01_jnt_1': (-18.0 * p + 5.0 * (1 - p), 0.0, -20.0 * p - 14.5 * (1 - p)),
            'R_sleeveD_01_jnt_4': (-8.0 * p, 0.0, 3.5 * p),
            'R_sleeveD_01_jnt_9': (-6.0 * p, 0.0, 4.0 * p),
            'R_sleeveD_01_jnt_15': (5.0 * p, 0.0, -3.0 * p),
            'tail_jnt2': (2.0, -6.0, 0.0),
            'tail_jnt4': (-6.0 * p + 4.0, -10.0, 0.0),
            'tail_jnt6': (-10.0 * p + 3.0, -9.0, 0.0),
            'tail_jnt8': (-8.0 * p, -6.0, 0.0),
        }
        sit_ov.update(hand_pose('L', 'lap_rest', scale=p))
        sit_ov.update(hand_pose('R', 'lap_rest', scale=p))
        sit_ov.update(seated_skirt_drape(p, pitch_scale=1.02))
        r = merge_rots(base, sit_ov)
        specs['sitting'].append({'rots': r, 'morphs': {'にこり': 0.26 * p, '口角上げ': 0.38 * p}, 'yaw': 3.0 * p})

    # 6. sleeping (4 frames, settles on frame 3): cozy peaceful seated nap with aligned boots & soft head nod
    slp_prog = [0.35, 0.65, 0.90, 1.0]
    specs['sleeping'] = []
    for p in slp_prog:
        slp_ov = {
            '腰': (-5.0 * p, 0.0, 0.0),
            '上半身': (-6.5 * p, 0.0, 3.2 * p),
            '上半身2': (-5.0 * p, 0.0, 3.8 * p),
            '首': (-6.5 * p, -2.0 * p, 5.5 * p),
            '頭': (-10.5 * p, -5.5 * p, 12.5 * p),
            # Keep knees gently together & both boot soles level with floor contact!
            '左足': (76.0 * p, 4.5 * p, -2.4 * p),
            '右足': (76.0 * p, -4.5 * p, 2.4 * p),
            '左ひざ': (-88.0 * p, 0.0, 2.6 * p),
            '右ひざ': (-88.0 * p, 0.0, -2.6 * p),
            '左足首': (-6.0 * p, 0.0, -1.2 * p),
            '右足首': (-6.0 * p, 0.0, 1.2 * p),
            # Cozy relaxed hands resting together on her lap
            '左腕': (1.0 * p, 10.0 * p, -28.0),
            '右腕': (1.0 * p, -10.0 * p, 28.0),
            '左ひじ': (56.0 * p + 10.0 * (1 - p), 32.0 * p, -32.0 * p),
            '右ひじ': (56.0 * p + 10.0 * (1 - p), -32.0 * p, 32.0 * p),
            '左手捩': (-18.0 * p, 12.0 * p, 0.0),
            '右手捩': (-18.0 * p, -12.0 * p, 0.0),
            '左手首': (-4.0 * p, 18.0 * p, -12.0 * p),
            '右手首': (-4.0 * p, -18.0 * p, 12.0 * p),
            # Soft S-curved cape drape
            'L_sleeveD_01_jnt_1': (-19.0 * p + 5.0 * (1 - p), 0.0, 20.0 * p + 14.5 * (1 - p)),
            'L_sleeveD_01_jnt_4': (-8.0 * p, 0.0, -3.5 * p),
            'L_sleeveD_01_jnt_9': (-6.0 * p, 0.0, -4.0 * p),
            'L_sleeveD_01_jnt_15': (5.0 * p, 0.0, 3.0 * p),
            'R_sleeveD_01_jnt_1': (-19.0 * p + 5.0 * (1 - p), 0.0, -20.0 * p - 14.5 * (1 - p)),
            'R_sleeveD_01_jnt_4': (-8.0 * p, 0.0, 3.5 * p),
            'R_sleeveD_01_jnt_9': (-6.0 * p, 0.0, 4.0 * p),
            'R_sleeveD_01_jnt_15': (5.0 * p, 0.0, -3.0 * p),
            'tail_jnt2': (2.0, -6.0, 0.0),
            'tail_jnt4': (-6.0 * p + 4.0, -10.0, 0.0),
            'tail_jnt6': (-10.0 * p + 3.0, -9.0, 0.0),
            'tail_jnt8': (-8.0 * p, -6.0, 0.0),
        }
        slp_ov.update(hand_pose('L', 'lap_rest', scale=p))
        slp_ov.update(hand_pose('R', 'lap_rest', scale=p))
        slp_ov.update(seated_skirt_drape(p, pitch_scale=1.06))
        r = merge_rots(base, slp_ov)
        # Use まばたき (clean closed eyelids) + subtle なごみ/口角上げ for peaceful sleeping face!
        specs['sleeping'].append({
            'rots': r,
            'morphs': {'まばたき': min(1.0, 0.50 + 0.50 * p), '口角上げ': 0.18 * p},
            'yaw': -12.0 * p,
        })

    # 7. jumping (5 frames): asymmetric graceful anime hop with cheerful outward balance arms & zero hand-skirt contact!
    jmp_params = [
        (0.0,   7.0, 20.0, -38.0, 22.0, -42.0, -20.0, 14.0, 0.18),
        (0.65, -7.0, 28.0, -54.0, 40.0, -76.0, -10.0, 10.0, 0.42),
        (1.05, -9.5, 32.0, -62.0, 46.0, -86.0,  -6.0,  8.0, 0.56),
        (0.80, -6.0, 26.0, -50.0, 36.0, -70.0, -11.0, 10.0, 0.46),
        (0.15,  4.5, 18.0, -32.0, 20.0, -36.0, -18.0, 13.0, 0.26),
    ]
    specs['jumping'] = []
    for lift, body_x, l_hip, l_knee, r_hip, r_knee, arm_raise, slv_z, smile in jmp_params:
        j_ov = {
            '腰': (0.0, -3.0, 0.0),
            '上半身': (body_x, 3.0, -1.5),
            '頭': (-body_x * 0.55 - 1.5, -3.0, -2.5),
            '左足': (l_hip, 2.0, -1.0),
            '右足': (r_hip, -2.0, 1.0),
            '左ひざ': (l_knee, 0.0, 0.0),
            '右ひざ': (r_knee, 0.0, 0.0),
            '左足首': (-14.0, 0.0, 0.0),
            '右足首': (-18.0, 0.0, 0.0),
            '左腕': (8.0, -6.0, arm_raise),
            '右腕': (8.0, 6.0, -arm_raise),
            '左ひじ': (28.0, 0.0, -12.0),
            '右ひじ': (28.0, 0.0, 12.0),
            'L_sleeveD_01_jnt_1': (4.0, 0.0, slv_z),
            'R_sleeveD_01_jnt_1': (4.0, 0.0, -slv_z),
            'L_hairE_jnt2': (1.8, -0.8, -3.5),
            'R_hairE_jnt2': (1.8, 0.8, 3.5),
        }
        j_ov.update(hand_pose('L', 'flustered', scale=0.65))
        j_ov.update(hand_pose('R', 'flustered', scale=0.65))
        j_ov.update(seated_skirt_drape(0.40, pitch_scale=0.95))
        r = merge_rots(base, j_ov)
        specs['jumping'].append({'rots': r, 'morphs': {'笑い': smile * 0.65, 'にこり': 0.25, '口角上げ': smile}, 'y_shift': lift, 'yaw': 4.0})

    # 8. review (6 frames, settles on frame 5): IK-solved delicate index fingertip under/beside chin, curled fingers, zero face block!
    specs['review'] = []
    for i in range(6):
        p = min(1.0, (i + 1) / 4.0)
        rev_ov = {
            '腰': (0.0, 2.0 * p, 0.8 * p),
            '上半身': (-2.0 * p, -6.0 * p, 1.5 * p),
            '頭': (4.5 * p - 2.0 * (1 - p), 8.0 * p, -7.5 * p),
            '両目': (-1.5 * p, -3.0 * p, 0.0),
            '右腕': (7.0 * p + 4.0 * (1 - p), -12.6 * p, 25.4 * p + 26.0 * (1 - p)),
            '右ひじ': (114.6 * p + 14.0 * (1 - p), -40.4 * p, 61.0 * p + 7.0 * (1 - p)),
            '右手捩': (-7.5 * p, -5.2 * p, 0.0),
            '右手首': (14.4 * p, -16.6 * p, 26.2 * p - 5.0 * (1 - p)),
            'R_sleeveD_01_jnt_1': (-8.0 * p + 5.0 * (1 - p), 0.0, -20.0),
            '左腕': (6.0, 4.0, -27.0),
            '左ひじ': (18.0, 0.0, -8.0),
        }
        rev_ov.update(hand_pose('R', 'chin_thought', scale=p))
        r = merge_rots(base, rev_ov)
        specs['review'].append({'rots': r, 'morphs': {'真面目': 0.42 * p, 'じと目': 0.20 * p, '口角上げ': 0.12}, 'yaw': -4.0 * p})

    # 9. running (6 frames, settles on frame 5) -> mapped to "think" / "special" (IK-solved cheerful raised-index Aha gesture!)
    specs['running'] = []
    for i in range(6):
        p = min(1.0, (i + 1) / 3.5)
        aha = 1.0 if i >= 3 else 0.0
        aha_ov = {
            '腰': (0.0, -2.5 * p, -1.0 * p),
            '上半身': (-2.0 * p, 6.0 * p, -2.0 * p),
            '頭': (-4.0 * aha + 3.5 * (1 - aha), -8.0 * p, 6.5 * p),
            '左腕': (19.0 * p + 4.0 * (1 - p), -11.5 * p, -21.0 * p - 26.0 * (1 - p)),
            '左ひじ': (121.0 * p + 14.0 * (1 - p), 51.5 * p, -55.0 * p - 7.0 * (1 - p)),
            '左手捩': (-38.5 * p, 26.8 * p, 0.0),
            '左手首': (21.5 * p, 7.5 * p, 8.5 * p + 5.0 * (1 - p)),
            'L_sleeveD_01_jnt_1': (-8.0 * p + 5.0 * (1 - p), 0.0, 20.0),
            '右腕': (8.0, 0.0, 27.0 - 4.0 * aha),
            '右ひじ': (20.0, 0.0, 8.0),
        }
        aha_ov.update(hand_pose('L', 'aha_point', scale=p))
        r = merge_rots(base, aha_ov)
        m = {'笑い': 0.32, 'にこり': 0.30, 'にっこり': 0.42, '口角上げ': 0.24} if aha else {'真面目': 0.32, '口角上げ': 0.12}
        specs['running'].append({'rots': r, 'morphs': m, 'yaw': 4.0 * p})

    # 10. waiting (6 frames): graceful contrapposto weight sway + soft blink
    specs['waiting'] = []
    for i in range(6):
        ph = i / 6.0 * 2.0 * math.pi
        sw = math.sin(ph)
        r = merge_rots(base, {
            '腰': (0.0, sw * 3.8, sw * 1.8),
            '上半身': (-0.5, -sw * 2.8, -sw * 1.8),
            '頭': (-1.5, sw * 3.8, -sw * 2.8),
            'L_sleeveD_01_jnt_2': (1.5, 0.0, 4.0 + sw * 2.0),
            'R_sleeveD_01_jnt_2': (1.5, 0.0, -4.0 + sw * 2.0),
            'tail_jnt8': (3.0, 8.0 + math.cos(ph) * 6.5, 0.0),
        })
        m = {'口角上げ': 0.18}
        if i == 3:
            m['まばたき'] = 0.92
        specs['waiting'].append({'rots': r, 'morphs': m, 'yaw': 2.0})

    # 11. failed (8 frames): expressive flustered defensive pose with softly curled hands & clear silhouette
    specs['failed'] = []
    for i in range(8):
        s = math.sin(i / 8.0 * 2.0 * math.pi)
        f_ov = {
            '腰': (4.0, 0.0, 0.0),
            '上半身': (-6.5, s * 6.5, s * 2.5),
            '頭': (-5.5, -s * 7.5, 7.5 + s * 1.5),
            '左腕': (14.0, 8.0, -22.0),
            '右腕': (14.0, -8.0, 22.0),
            '左ひじ': (82.0, 24.0, -42.0),
            '右ひじ': (82.0, -24.0, 42.0),
            '左手捩': (-20.0, 14.0, 0.0),
            '右手捩': (-20.0, -14.0, 0.0),
            '左足': (6.0, 2.0, -1.0),
            '右足': (6.0, -2.0, 1.0),
            '左ひざ': (-8.0, 0.0, 0.0),
            '右ひざ': (-8.0, 0.0, 0.0),
            'L_sleeveD_01_jnt_1': (2.0, 0.0, 18.0),
            'R_sleeveD_01_jnt_1': (2.0, 0.0, -18.0),
        }
        f_ov.update(hand_pose('L', 'flustered'))
        f_ov.update(hand_pose('R', 'flustered'))
        r = merge_rots(base, f_ov)
        specs['failed'].append({'rots': r, 'morphs': {'困る': 1.0, 'ω': 0.50, '口角下げ': 0.25}})

    # 12. look (16 frames, index 0..15 clockwise from top: 0=UP, 4=RIGHT(+X), 8=DOWN, 12=LEFT(-X))
    specs['look'] = []
    for i in range(16):
        ang = math.radians(i * 22.5)
        sx_dir = math.sin(ang)   # +1 = screen right (+X), -1 = screen left (-X)
        sy_dir = math.cos(ang)   # +1 = screen up (+Y), -1 = screen down (-Y)
        head_yaw = -sx_dir * 17.5
        head_pitch = sy_dir * 11.5
        head_roll = -sx_dir * 4.2
        eye_yaw = -sx_dir * 7.5
        eye_pitch = sy_dir * 6.5
        r = merge_rots(base, {
            '腰': (0.0, 1.0 - sx_dir * 1.5, 0.5),
            '上半身': (-0.5 + sy_dir * 1.0, -sx_dir * 2.5, 0.0),
            '上半身2': (sy_dir * 1.8, -sx_dir * 3.8, -sx_dir * 0.8),
            '首': (head_pitch * 0.28, head_yaw * 0.28, head_roll * 0.25),
            '頭': (head_pitch * 0.78, head_yaw * 0.78, head_roll * 0.80),
            '両目': (eye_pitch, eye_yaw, 0.0),
        })
        specs['look'].append({'rots': r, 'morphs': {'口角上げ': 0.16}, 'yaw': 2.0})

    return specs


def render_portraits_and_icon(model: PMXModel, samples: int = 8) -> List[Path]:
    """Render 512x512 half-body expression portraits and a crisp multi-size si.ico."""
    from PIL import ImageDraw

    base = base_drape()
    p_ren = GLPMXRenderer(model, width=512, height=512, samples=samples)
    out_dir = ASSETS_DIR / 'portraits'
    out_dir.mkdir(parents=True, exist_ok=True)

    think_rots = merge_rots(base, {
        '上半身': (-2.0, -6.0, 1.5),
        '頭': (4.5, 8.0, -7.5),
        '両目': (-1.5, -3.0, 0.0),
        '右腕': (7.0, -12.6, 25.4),
        '右ひじ': (114.6, -40.4, 61.0),
        '右手捩': (-7.5, -5.2, 0.0),
        '右手首': (14.4, -16.6, 26.2),
        'R_sleeveD_01_jnt_1': (-8.0, 0.0, -20.0),
    })
    think_rots.update(hand_pose('R', 'chin_thought'))

    portrait_specs = {
        'neutral': {
            'rots': merge_rots(base, {'上半身': (-0.6, -1.2, 0.0), '頭': (-2.2, -2.2, -1.2)}),
            'morphs': {'口角上げ': 0.18},
            'yaw': 2.5,
        },
        'happy': {
            'rots': merge_rots(base, {'上半身': (-2.0, 3.5, 1.0), '頭': (-3.2, -4.5, -3.8)}),
            'morphs': {'笑い': 0.24, 'にこり': 0.32, 'にっこり': 0.40, '口角上げ': 0.22},
            'yaw': 4.0,
        },
        'blush': {
            'rots': merge_rots(base, {'上半身': (1.5, -3.0, 1.8), '頭': (4.2, 4.8, 5.5), '両目': (-2.0, 3.0, 0.0)}),
            'morphs': {'にこり': 0.38, 'にっこり': 0.35, '口角上げ': 0.20, 'まばたき': 0.18},
            'yaw': -4.0,
        },
        'thinking': {
            'rots': think_rots,
            'morphs': {'真面目': 0.42, 'じと目': 0.20, '口角上げ': 0.12},
            'yaw': -5.0,
        },
        'sleepy': {
            'rots': merge_rots(base, {'上半身': (-4.0, 0.0, 2.8), '首': (-4.0, 0.0, 4.0), '頭': (-6.5, -4.5, 8.5)}),
            'morphs': {'まばたき': 0.92, '口角上げ': 0.18},
            'yaw': -7.0,
        },
        'troubled': {
            'rots': merge_rots(base, {'上半身': (-4.5, 4.0, 2.0), '頭': (-5.5, -5.5, 6.8)}),
            'morphs': {'困る': 1.0, 'ω': 0.48, '口角下げ': 0.22},
            'yaw': 4.0,
        },
    }

    written: List[Path] = []
    for name, sp in portrait_specs.items():
        im = p_ren.render(
            sp['rots'],
            morph_weights=sp['morphs'],
            yaw_deg=sp['yaw'],
            y_view_range=8.4,
            bottom_px=512.0,
            anchor_y=14.9,
            draw_shadow=False,
        )
        dest = out_dir / f'{name}.png'
        im.save(dest, optimize=True)
        written.append(dest)

    # Render a tighter head/horn zoom for crisp 16-256px taskbar/tray badge icon
    head_im = p_ren.render(
        merge_rots(base, {'頭': (-2.0, -3.5, -1.5)}),
        morph_weights={'にこり': 0.24, '口角上げ': 0.36},
        yaw_deg=4.5,
        y_view_range=6.5,
        bottom_px=505.0,
        anchor_y=16.5,
        draw_shadow=False,
    )
    badge = Image.new('RGBA', (512, 512), (0, 0, 0, 0))
    draw = ImageDraw.Draw(badge)
    draw.rounded_rectangle((18, 18, 494, 494), radius=116, fill=(28, 36, 34, 255), outline=(202, 162, 78, 255), width=18)
    draw.ellipse((46, 46, 466, 466), fill=(40, 56, 51, 255), outline=(122, 164, 143, 180), width=6)

    mask = Image.new('L', (512, 512), 0)
    ImageDraw.Draw(mask).rounded_rectangle((28, 28, 484, 484), radius=108, fill=255)
    head_clipped = Image.composite(head_im, Image.new('RGBA', (512, 512), (0, 0, 0, 0)), mask)
    badge.alpha_composite(head_clipped)

    ico_path = ASSETS_DIR / 'si.ico'
    badge.save(
        ico_path,
        format='ICO',
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)],
    )
    badge_png = out_dir / 'icon_badge.png'
    badge.resize((128, 128), Image.Resampling.LANCZOS).save(badge_png, optimize=True)
    written.extend([ico_path, badge_png])
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description='Render 祀 3D MMD sprites for si-desktop-pet.')
    parser.add_argument('--pmx', type=Path, default=PMX_DEFAULT)
    parser.add_argument('--out-dir', type=Path, default=ASSETS_DIR / 'hd')
    parser.add_argument('--preview', type=Path, default=None, help='Save 8-pose comparison preview image instead of full export.')
    parser.add_argument('--portraits-only', action='store_true', help='Render portraits and icon only.')
    parser.add_argument('--samples', type=int, default=8)
    parser.add_argument('--cool-ms', type=int, default=20, help='Thermal safety yield in ms between frames.')
    args = parser.parse_args()

    if not args.pmx.exists():
        print(f'ERROR: PMX not found: {args.pmx}', file=sys.stderr)
        return 1

    app = QGuiApplication(sys.argv)
    t0 = time.time()
    model = PMXModel(args.pmx)
    print(f'Loaded {args.pmx.name} ({len(model.positions)} verts, {len(model.faces)} tris) in {time.time() - t0:.2f}s')

    if args.portraits_only:
        written = render_portraits_and_icon(model, samples=args.samples)
        print(f'Rendered {len(written)} portrait/icon assets into {ASSETS_DIR}')
        return 0

    renderer = GLPMXRenderer(model, width=MASTER_W, height=MASTER_H, samples=args.samples)
    specs = build_state_specs()

    if args.preview is not None:
        picks = [
            ('idle/00', specs['idle'][0]),
            ('waving/01', specs['waving'][1]),
            ('review/05', specs['review'][5]),
            ('running/05', specs['running'][5]),
            ('sitting/03', specs['sitting'][3]),
            ('sleeping/03', specs['sleeping'][3]),
            ('walk-right/02', specs['walk-right'][2]),
            ('running-right/02', specs['running-right'][2]),
        ]
        tile_w, tile_h = 480, 520
        sheet = Image.new('RGBA', (tile_w * 4, tile_h * 2), (242, 240, 234, 255))
        for idx, (label, spec) in enumerate(picks):
            im = renderer.render(
                spec['rots'],
                spec.get('trans'),
                spec.get('morphs'),
                yaw_deg=spec.get('yaw', 0.0),
                world_y_shift=spec.get('y_shift', 0.0),
            )
            thumb = im.resize((tile_w, tile_h), Image.Resampling.LANCZOS)
            cx = (idx % 4) * tile_w
            cy = (idx // 4) * tile_h
            sheet.alpha_composite(thumb, (cx, cy))
        args.preview.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(args.preview)
        idle_path = args.preview.with_name(args.preview.stem + '_idle.png')
        im0 = renderer.render(
            specs['idle'][0]['rots'],
            morph_weights=specs['idle'][0].get('morphs'),
            yaw_deg=specs['idle'][0].get('yaw', 0.0),
        )
        im0.save(idle_path)
        print(f'Saved preview sheet -> {args.preview} and idle -> {idle_path}')
        return 0

    manifest = json.loads((ASSETS_DIR / 'animations.json').read_text(encoding='utf-8'))
    t1 = time.time()
    count = 0
    cool_sec = max(0.0, args.cool_ms / 1000.0)
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
            bbox = im.getchannel('A').getbbox()
            if bbox is None or bbox[1] <= 0 or bbox[3] >= MASTER_H:
                raise RuntimeError(f'Framing out of bounds for {f_rel}: bbox={bbox}')
            dest = ASSETS_DIR / f_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            im.save(dest, optimize=True)
            count += 1
            if cool_sec > 0:
                time.sleep(cool_sec)
    p_written = render_portraits_and_icon(model, samples=args.samples)
    print(f'Rendered {count} frames at {MASTER_W}x{MASTER_H} and {len(p_written)} portrait/icon assets in {time.time() - t1:.2f}s')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
