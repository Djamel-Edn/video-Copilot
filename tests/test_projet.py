import copy

import pytest

from moteur.projet import OpError, Projet, nouveau_document


def projet_avec_media(duree=20.0):
    p = Projet(nouveau_document())
    p.appliquer([{"op": "add_media", "media": {"id": "m1", "name": "a.mp4", "path": "a.mp4", "kind": "video",
                                               "duration": duree, "has_audio": True, "proxy": None}}],
                internal=True)
    p.undo_stack.clear()
    return p


def test_ajout_et_valeurs_par_defaut():
    p = projet_avec_media()
    (cid,), _ = p.appliquer([{"op": "add_clip", "media": "m1", "start": 2}])
    c = p.doc["clips"][cid]
    assert c["track"] == "V1" and c["duration"] == 20.0 and c["props"]["opacity"] == 1.0


def test_chevauchement_refuse_puis_overwrite():
    p = projet_avec_media()
    p.appliquer([{"op": "add_clip", "media": "m1", "start": 0, "duration": 10, "id": "a"}])
    with pytest.raises(OpError, match="Chevauchement"):
        p.appliquer([{"op": "add_clip", "media": "m1", "start": 5, "duration": 2}])
    p.appliquer([{"op": "add_clip", "media": "m1", "start": 5, "duration": 2, "overwrite": True, "id": "b"}])
    clips = sorted(p.doc["clips"].values(), key=lambda c: c["start"])
    assert [(c["start"], c["duration"]) for c in clips] == [(0, 5), (5, 2), (7, 3)]
    assert clips[2]["in"] == pytest.approx(7)


def test_lot_atomique():
    p = projet_avec_media()
    avant = copy.deepcopy(p.doc)
    with pytest.raises(OpError, match="Opération 1"):
        p.appliquer([{"op": "add_clip", "media": "m1", "start": 0, "duration": 5},
                     {"op": "add_effect", "clip": "inexistant", "effect": "blur"}])
    assert p.doc == avant and not p.undo_stack


def test_reference_dollar_et_annulation_en_bloc():
    p = projet_avec_media()
    avant = copy.deepcopy(p.doc)
    p.appliquer([{"op": "add_clip", "media": "m1", "start": 0, "duration": 5},
                 {"op": "add_effect", "clip": "$0", "effect": "blur", "params": {"radius": 12}},
                 {"op": "set_keyframes", "clip": "$0", "prop": "fx.$1.radius".replace("$1", "fx2"),
                  "keys": [{"t": 0, "v": 0}, {"t": 2, "v": 20, "ease": "ease_out"}]}], label="flou animé")
    apres = copy.deepcopy(p.doc)
    p.annuler()
    assert p.doc == avant
    p.retablir()
    assert p.doc == apres


def test_cut_range_ripple():
    p = projet_avec_media()
    p.appliquer([{"op": "add_clip", "media": "m1", "start": 0, "duration": 10, "id": "a"},
                 {"op": "add_clip", "media": "m1", "start": 10, "in": 10, "duration": 5, "id": "b"}])
    p.appliquer([{"op": "cut_range", "start": 3, "end": 5}])
    clips = sorted(p.doc["clips"].values(), key=lambda c: c["start"])
    assert [(c["start"], c["duration"], c["in"]) for c in clips] == [(0, 3, 0), (3, 5, 5), (8, 5, 10)]


def test_scinder_decale_les_images_cles():
    p = projet_avec_media()
    p.appliquer([{"op": "add_clip", "media": "m1", "start": 0, "duration": 10, "id": "a"},
                 {"op": "set_keyframes", "clip": "a", "prop": "opacity", "keys": [{"t": 6, "v": 0}]}])
    (rid,), _ = p.appliquer([{"op": "split_clip", "clip": "a", "at": 4}])
    assert p.doc["clips"][rid]["keyframes"]["opacity"][0]["t"] == pytest.approx(2)
    assert p.doc["clips"][rid]["in"] == pytest.approx(4)


def test_source_depassee():
    p = projet_avec_media(duree=8)
    with pytest.raises(OpError, match="dépasse la source"):
        p.appliquer([{"op": "add_clip", "media": "m1", "start": 0, "in": 5, "duration": 5}])


def test_effet_sur_mesure():
    p = projet_avec_media()
    glsl = "vec4 effect(vec2 uv){ return src(uv) * p_gain; }"
    p.appliquer([{"op": "define_effect", "name": "gain", "glsl": glsl,
                  "params": {"gain": {"type": "float", "default": 1, "min": 0, "max": 4}}},
                 {"op": "add_clip", "media": "m1", "start": 0, "duration": 5, "id": "a"},
                 {"op": "add_effect", "clip": "a", "effect": "gain", "params": {"gain": 2}}])
    assert p.doc["clips"]["a"]["effects"][0]["params"]["gain"] == 2
    with pytest.raises(OpError, match="main"):
        p.appliquer([{"op": "define_effect", "name": "x2", "glsl": "void main(){} vec4 effect(vec2 uv){}"}])
    with pytest.raises(OpError, match="intégré"):
        p.appliquer([{"op": "define_effect", "name": "blur", "glsl": glsl}])


def test_texte_et_couleur():
    p = Projet()
    p.appliquer([{"op": "add_clip", "type": "text", "start": 0, "text": {"content": "Salut", "size": 120}},
                 {"op": "add_clip", "type": "solid", "track": "V2", "start": 0, "color": "#ff0000"}])
    types = sorted(c["type"] for c in p.doc["clips"].values())
    assert types == ["solid", "text"]
    with pytest.raises(OpError):
        p.appliquer([{"op": "add_clip", "type": "text", "track": "A1", "start": 20}])


def test_choix_gpu():
    from moteur.gpu import Gpu, choisir
    assert choisir([Gpu("nvidia", "GTX 1650", 4096)]).niveau == "léger"
    assert choisir([Gpu("nvidia", "RTX 4070", 12282)]).niveau == "moyen"
    assert choisir([Gpu("nvidia", "RTX 4090", 24564)]).niveau == "haut"
    assert choisir([Gpu("nvidia", "GT 710", 2048)]).accel == "cpu"
    assert choisir([Gpu("amd", "Radeon RX 6600", 0)]).accel == "directml"
    assert choisir([]).accel == "cpu"


def test_image_cle_unique_dupliquer_ripple():
    p = projet_avec_media()
    p.appliquer([{"op": "add_clip", "media": "m1", "start": 0, "duration": 4, "id": "a"},
                 {"op": "add_clip", "media": "m1", "start": 4, "in": 4, "duration": 4, "id": "b"},
                 {"op": "set_keyframe", "clip": "a", "prop": "scale", "t": 1, "v": 1.2, "ease": "ease_out"},
                 {"op": "set_keyframe", "clip": "a", "prop": "scale", "t": 1, "v": 1.5}])
    ks = p.doc["clips"]["a"]["keyframes"]["scale"]
    assert ks == [{"t": 1.0, "v": 1.5, "ease": "ease_out"}]
    p.appliquer([{"op": "remove_keyframe", "clip": "a", "prop": "scale", "t": 1}])
    assert "scale" not in p.doc["clips"]["a"]["keyframes"]
    (d,), _ = p.appliquer([{"op": "duplicate_clip", "clip": "b"}])
    assert p.doc["clips"][d]["start"] == 8
    p.appliquer([{"op": "ripple_delete", "clips": ["a"]}])
    assert sorted(c["start"] for c in p.doc["clips"].values()) == [0, 4]


def test_texte_complet():
    p = Projet()
    (cid,), _ = p.appliquer([{"op": "add_clip", "type": "text", "start": 0, "text": {
        "content": "Ombre", "italic": True, "letter_spacing": -2, "shadow_opacity": 0.6,
        "background": "#101010", "background_opacity": 0.5, "uppercase": True}}])
    assert p.doc["clips"][cid]["text"]["italic"] is True
    with pytest.raises(OpError):
        p.appliquer([{"op": "update_clip", "clip": cid, "text": {"weight": 1200}}])


def test_montage_par_le_texte():
    from moteur.texte import mots_timeline, plages_hesitations, plages_silences, ops_couper
    p = projet_avec_media()
    p.appliquer([{"op": "add_clip", "media": "m1", "start": 0, "duration": 10, "id": "a"}])
    words = [{"w": "Bonjour", "s": 0.5, "e": 1.0}, {"w": "euh,", "s": 1.2, "e": 1.5},
             {"w": "tout", "s": 1.7, "e": 2.0}, {"w": "le", "s": 4.0, "e": 4.2}, {"w": "monde", "s": 4.3, "e": 4.8}]
    mots = mots_timeline(p.doc, {"m1": words})
    assert [m["w"] for m in mots] == ["Bonjour", "euh,", "tout", "le", "monde"]
    h = plages_hesitations(mots)
    assert len(h) == 1 and 1.1 <= h[0][0] < 1.2 and 1.5 < h[0][1] <= 1.6
    s = plages_silences(mots, seuil=0.7, garder=0.2)
    assert s == [(2.1, 3.9)]
    p.appliquer(ops_couper(h + s))
    mots2 = mots_timeline(p.doc, {"m1": words})
    assert [m["w"] for m in mots2] == ["Bonjour", "tout", "le", "monde"]
    gap = mots2[2]["start"] - mots2[1]["end"]
    assert abs(gap - 0.2) < 0.02


def test_rotation_des_videos_de_telephone():
    from moteur.medias import rotation
    assert rotation({"side_data_list": [{"rotation": 90}]}) == 90
    assert rotation({"side_data_list": [{"rotation": -90}]}) == 270
    assert rotation({"side_data_list": [{"side_data_type": "Display Matrix", "rotation": 180.0}]}) == 180
    assert rotation({"tags": {"rotate": "270"}}) == 270
    assert rotation({}) == 0
