"""Bibliothèque d'effets intégrés.

Chaque effet suit exactement le même contrat que les effets écrits par l'IA
(voir ARCHITECTURE.md) : une fonction GLSL `vec4 effect(vec2 uv)`, des
paramètres `p_<nom>`, et les aides `src(uv)`, `below(uv)`, `u_time`, `u_dur`,
`u_res`. Le moteur n'a donc aucun cas particulier pour les intégrés.
"""

CONTRAT_GLSL = """\
Écris uniquement la fonction `vec4 effect(vec2 uv)` (GLSL ES 3.00), plus
d'éventuelles fonctions d'aide au-dessus. Disponible :
  src(uv)    -> vec4 : le calque du clip (après position/échelle/rotation)
  below(uv)  -> vec4 : l'image déjà composée SOUS ce clip
  uv         : (0,0) = bas-gauche, (1,1) = haut-droit
  u_time     : secondes depuis le début du clip
  u_dur      : durée du clip en secondes
  u_res      : résolution du projet en pixels (vec2)
  p_<nom>    : chaque paramètre déclaré (float, ou vec3 pour type "color")
Couleurs en alpha non prémultiplié. Hors du cadre du clip, src() a alpha 0.
blend "over" : le résultat se superpose. blend "replace" : il remplace
l'image composée (transitions, modes de fusion : utiliser below()).
Pas de #version, pas de uniform à déclarer, pas de main().
"""


def _f(default, lo, hi, label=""):
    return {"type": "float", "default": default, "min": lo, "max": hi, "label": label}


def _c(default, label=""):
    return {"type": "color", "default": default, "label": label}


BUILTIN = {
    "brightness_contrast": {
        "label": "Luminosité / contraste",
        "params": {"brightness": _f(0.0, -1, 1, "Luminosité"), "contrast": _f(1.0, 0, 3, "Contraste")},
        "glsl": """
vec4 effect(vec2 uv) {
    vec4 c = src(uv);
    c.rgb = (c.rgb - 0.5) * p_contrast + 0.5 + p_brightness;
    return vec4(clamp(c.rgb, 0.0, 1.0), c.a);
}""",
    },
    "saturation": {
        "label": "Saturation",
        "params": {"amount": _f(1.0, 0, 3, "Saturation")},
        "glsl": """
vec4 effect(vec2 uv) {
    vec4 c = src(uv);
    float g = dot(c.rgb, vec3(0.2126, 0.7152, 0.0722));
    return vec4(clamp(mix(vec3(g), c.rgb, p_amount), 0.0, 1.0), c.a);
}""",
    },
    "black_white": {
        "label": "Noir et blanc",
        "params": {"amount": _f(1.0, 0, 1, "Intensité")},
        "glsl": """
vec4 effect(vec2 uv) {
    vec4 c = src(uv);
    float g = dot(c.rgb, vec3(0.2126, 0.7152, 0.0722));
    return vec4(mix(c.rgb, vec3(g), p_amount), c.a);
}""",
    },
    "tint": {
        "label": "Teinte",
        "params": {"color": _c([1.0, 0.6, 0.3], "Couleur"), "amount": _f(0.3, 0, 1, "Intensité")},
        "glsl": """
vec4 effect(vec2 uv) {
    vec4 c = src(uv);
    return vec4(mix(c.rgb, c.rgb * p_color * 1.5, p_amount), c.a);
}""",
    },
    "blur": {
        "label": "Flou",
        "params": {"radius": _f(8.0, 0, 80, "Rayon (px)")},
        "glsl": """
vec4 effect(vec2 uv) {
    if (p_radius < 0.5) return src(uv);
    vec2 px = p_radius / u_res / 4.0;
    vec4 acc = vec4(0.0); float w = 0.0;
    for (int i = -4; i <= 4; i++) {
        for (int j = -4; j <= 4; j++) {
            float k = exp(-float(i * i + j * j) / 8.0);
            acc += src(uv + vec2(float(i), float(j)) * px) * k;
            w += k;
        }
    }
    return acc / w;
}""",
    },
    "sharpen": {
        "label": "Netteté",
        "params": {"amount": _f(0.5, 0, 3, "Intensité")},
        "glsl": """
vec4 effect(vec2 uv) {
    vec2 d = 1.0 / u_res;
    vec4 c = src(uv);
    vec4 n = src(uv + vec2(d.x, 0.0)) + src(uv - vec2(d.x, 0.0))
           + src(uv + vec2(0.0, d.y)) + src(uv - vec2(0.0, d.y));
    return vec4(clamp(c.rgb + (c.rgb * 4.0 - n.rgb) * p_amount, 0.0, 1.0), c.a);
}""",
    },
    "vignette": {
        "label": "Vignette",
        "params": {"amount": _f(0.6, 0, 1, "Intensité"), "radius": _f(0.45, 0, 1, "Rayon"),
                   "softness": _f(0.35, 0.01, 1, "Douceur")},
        "glsl": """
vec4 effect(vec2 uv) {
    vec4 c = src(uv);
    vec2 p = (uv - 0.5) * vec2(u_res.x / u_res.y, 1.0);
    float v = smoothstep(p_radius, p_radius + p_softness, length(p));
    return vec4(c.rgb * (1.0 - p_amount * v), c.a);
}""",
    },
    "chroma_key": {
        "label": "Incrustation (fond vert)",
        "params": {"key": _c([0.0, 1.0, 0.0], "Couleur à retirer"), "threshold": _f(0.18, 0, 1, "Seuil"),
                   "smoothness": _f(0.08, 0, 0.5, "Douceur")},
        "glsl": """
vec2 cbcr(vec3 c) {
    return vec2(-0.1687 * c.r - 0.3313 * c.g + 0.5 * c.b, 0.5 * c.r - 0.4187 * c.g - 0.0813 * c.b);
}
vec4 effect(vec2 uv) {
    vec4 c = src(uv);
    float d = distance(cbcr(c.rgb), cbcr(p_key));
    float a = smoothstep(p_threshold, p_threshold + p_smoothness, d);
    return vec4(c.rgb, c.a * a);
}""",
    },
    "rgb_split": {
        "label": "Décalage RVB",
        "params": {"amount": _f(6.0, 0, 60, "Décalage (px)"), "angle": _f(0.0, -180, 180, "Angle (°)")},
        "glsl": """
vec4 effect(vec2 uv) {
    float a = radians(p_angle);
    vec2 o = vec2(cos(a), sin(a)) * p_amount / u_res;
    vec4 c = src(uv);
    return vec4(src(uv + o).r, c.g, src(uv - o).b, c.a);
}""",
    },
    "pixelate": {
        "label": "Pixellisation",
        "params": {"size": _f(16.0, 1, 200, "Taille (px)")},
        "glsl": """
vec4 effect(vec2 uv) {
    vec2 cell = max(p_size, 1.0) / u_res;
    return src((floor(uv / cell) + 0.5) * cell);
}""",
    },
    "shake": {
        "label": "Tremblement caméra",
        "params": {"amplitude": _f(12.0, 0, 100, "Amplitude (px)"), "frequency": _f(8.0, 0.1, 30, "Fréquence (Hz)")},
        "glsl": """
vec4 effect(vec2 uv) {
    float t = u_time * p_frequency * 6.2831;
    vec2 o = vec2(sin(t) + 0.5 * sin(2.3 * t + 1.7), cos(1.3 * t) + 0.5 * sin(3.1 * t + 0.4));
    return src(uv + o * 0.5 * p_amplitude / u_res);
}""",
    },
    "grain": {
        "label": "Grain film",
        "params": {"amount": _f(0.08, 0, 0.5, "Intensité")},
        "glsl": """
float hash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
vec4 effect(vec2 uv) {
    vec4 c = src(uv);
    float n = hash(floor(uv * u_res) + floor(u_time * 24.0) * 17.0) - 0.5;
    return vec4(clamp(c.rgb + n * p_amount, 0.0, 1.0), c.a);
}""",
    },
    "glitch": {
        "label": "Glitch",
        "params": {"intensity": _f(0.5, 0, 1, "Intensité"), "speed": _f(12.0, 0, 60, "Vitesse")},
        "glsl": """
float hash(float x) { return fract(sin(x * 91.345) * 47453.21); }
vec4 effect(vec2 uv) {
    float t = floor(u_time * p_speed);
    float band = floor(uv.y * 24.0);
    float r = hash(band + t * 13.0);
    float shift = (r > 1.0 - 0.35 * p_intensity) ? (hash(band * 7.0 + t) - 0.5) * 0.12 * p_intensity : 0.0;
    vec2 q = vec2(uv.x + shift, uv.y);
    vec4 c = src(q);
    float s = 0.012 * p_intensity;
    return vec4(src(q + vec2(s, 0.0)).r, c.g, src(q - vec2(s, 0.0)).b, c.a);
}""",
    },
    "crop": {
        "label": "Recadrage",
        "params": {"left": _f(0.0, 0, 1, "Gauche"), "right": _f(0.0, 0, 1, "Droite"),
                   "top": _f(0.0, 0, 1, "Haut"), "bottom": _f(0.0, 0, 1, "Bas")},
        "glsl": """
vec4 effect(vec2 uv) {
    vec4 c = src(uv);
    bool inside = uv.x >= p_left && uv.x <= 1.0 - p_right && uv.y >= p_bottom && uv.y <= 1.0 - p_top;
    return inside ? c : vec4(c.rgb, 0.0);
}""",
    },
    "blend_mode": {
        "label": "Mode de fusion",
        "params": {"mode": _f(1.0, 0, 4, "0 normal · 1 produit · 2 écran · 3 incrustation · 4 différence")},
        "blend": "replace",
        "glsl": """
vec4 effect(vec2 uv) {
    vec4 s = src(uv); vec3 b = below(uv).rgb; vec3 a = s.rgb; vec3 r;
    int m = int(p_mode + 0.5);
    if (m == 1) r = a * b;
    else if (m == 2) r = 1.0 - (1.0 - a) * (1.0 - b);
    else if (m == 3) r = mix(2.0 * a * b, 1.0 - 2.0 * (1.0 - a) * (1.0 - b), step(0.5, b));
    else if (m == 4) r = abs(a - b);
    else r = a;
    return vec4(mix(b, r, s.a), 1.0);
}""",
    },
}

CATEGORIES = {
    "brightness_contrast": "Couleur", "saturation": "Couleur", "black_white": "Couleur", "tint": "Couleur",
    "blur": "Flou et netteté", "sharpen": "Flou et netteté",
    "vignette": "Stylisation", "grain": "Stylisation", "glitch": "Stylisation", "rgb_split": "Stylisation",
    "pixelate": "Stylisation", "shake": "Mouvement",
    "chroma_key": "Incrustation et masque", "crop": "Incrustation et masque", "blend_mode": "Fusion",
}

for _name, _d in BUILTIN.items():
    _d["category"] = CATEGORIES.get(_name, "Divers")
    _d.setdefault("blend", "over")
    _d["name"] = _name
    _d["builtin"] = True
    _d["glsl"] = _d["glsl"].strip() + "\n"
