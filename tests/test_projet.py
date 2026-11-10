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


def test_separer_le_son_et_clips_lies():
    p = projet_avec_media()
    p.appliquer([{"op": "add_clip", "media": "m1", "start": 0, "duration": 10, "id": "v",
                  "props": {"volume": 0.8}},
                 {"op": "set_keyframes", "clip": "v", "prop": "volume", "keys": [{"t": 0, "v": 0}, {"t": 1, "v": 0.8}]}])
    (aid,), _ = p.appliquer([{"op": "separate_audio", "clip": "v"}])
    v, a = p.doc["clips"]["v"], p.doc["clips"][aid]
    assert a["track"] == "A1" and a["link"] == v["link"] and v["audio_muted"] is True
    assert a["props"]["volume"] == 0.8 and "volume" in a["keyframes"] and "volume" not in v["keyframes"]
    with pytest.raises(OpError, match="déjà séparé"):
        p.appliquer([{"op": "separate_audio", "clip": "v"}])
    with pytest.raises(OpError, match="audio"):
        p.appliquer([{"op": "add_effect", "clip": aid, "effect": "blur"}])
    with pytest.raises(OpError):
        p.appliquer([{"op": "update_clip", "clip": aid, "track": "V2"}])
    # scinder l'un scinde l'autre ; les moitiés droites restent liées entre elles
    (rv,), _ = p.appliquer([{"op": "split_clip", "clip": "v", "at": 4}])
    droites = [c for c in p.doc["clips"].values() if c["start"] == 4]
    assert len(droites) == 2 and droites[0]["link"] == droites[1]["link"] != v["link"]
    # une coupe avec resserrage garde les paires cohérentes
    p.appliquer([{"op": "cut_range", "start": 6, "end": 7}])
    groupes = {}
    for c in p.doc["clips"].values():
        groupes.setdefault(c["link"], []).append((c["track"], round(c["start"], 3), round(c["duration"], 3)))
    assert all(len(g) == 2 and g[0][1:] == g[1][1:] for g in groupes.values()), groupes
    # dupliquer la paire dans un même lot : nouvelle paire liée, distincte de l'originale
    r, _ = p.appliquer([{"op": "duplicate_clip", "clip": "v", "start": 20},
                        {"op": "duplicate_clip", "clip": aid, "start": 20}])
    assert p.doc["clips"][r[0]]["link"] == p.doc["clips"][r[1]]["link"] != v["link"]
    p.appliquer([{"op": "unlink_clips", "clips": r}])
    assert p.doc["clips"][r[0]]["link"] is None


def test_sous_titres():
    from moteur import sous_titres as st
    assert st.joindre(["on", "n", "'a", "jamais", "vu", ",", "ça", "!"]) == "on n'a jamais vu, ça!"
    mots = [{"w": w, "start": i * 0.4, "end": i * 0.4 + 0.3} for i, w in enumerate(
        "En France les cas de cancer de la peau ont plus que triplé depuis 1990. Pourtant on utilise".split())]
    caps = st.decouper(mots, max_chars=20, max_lines=2)
    assert caps[0]["text"].count("\n") <= 1 and all(len(l) <= 20 for c in caps for l in c["text"].split("\n"))
    assert any(c["text"].endswith("1990.") for c in caps)            # coupure en fin de phrase
    assert all(a["end"] <= b["start"] for a, b in zip(caps, caps[1:]))  # aucun chevauchement
    courts = st.decouper(mots, max_chars=16, max_lines=1, max_words=3)
    assert all(len(c["text"].split()) <= 3 for c in courts)
    p = Projet()
    p.appliquer(st.ops(p.doc, caps, "classique"))
    tr = st.piste(p.doc)
    assert tr and tr["kind"] == "video" and tr is p.doc["tracks"][-1]
    assert sum(c["track"] == tr["id"] for c in p.doc["clips"].values()) == len(caps)
    p.appliquer(st.ops(p.doc, courts, "shorts"))                     # régénérer remplace
    assert sum(c["track"] == tr["id"] for c in p.doc["clips"].values()) == len(courts)
    srt = st.srt(p.doc)
    assert srt.startswith("1\n00:00:00,000 --> ") and "-->" in srt.split("\n\n")[-1]


def test_musique_sous_la_voix():
    from moteur.audio import zones_depuis_mots, zones_depuis_pics, cles_baisse
    mots = [{"start": 1.0, "end": 1.4}, {"start": 1.5, "end": 2.0}, {"start": 5.0, "end": 6.0}]
    z = zones_depuis_mots(mots)
    assert z == [(1.0, 2.0), (5.0, 6.0)]
    clip = {"start": 0.0, "duration": 10.0, "props": {"volume": 1.0}}
    keys = cles_baisse(clip, z, profondeur_db=-12, attaque=0.25, relache=0.5)
    vals = {k["t"]: k["v"] for k in keys}
    assert vals[0.75] == 1.0 and abs(vals[1.0] - 0.2512) < 1e-3 and vals[2.5] == 1.0
    assert abs(vals[6.0] - 0.2512) < 1e-3 and vals[6.5] == 1.0
    # zones proches fusionnées : pas de remontée entre les deux
    keys2 = cles_baisse(clip, [(1.0, 2.0), (2.3, 3.0)])
    assert [k["t"] for k in keys2] == [0.75, 1.0, 3.0, 3.5]
    # forme d'onde : du son de 1 à 2 s seulement
    pics = {"m": {"per_sec": 10, "peaks": [0.0] * 10 + [0.5] * 10 + [0.0] * 20}}
    zv = zones_depuis_pics([{"start": 0, "duration": 4, "in": 0, "speed": 1, "media": "m", "props": {"volume": 1}}], pics)
    assert zv == [(1.0, 2.0)]


def test_transitions():
    from moteur.projet import spans
    p = projet_avec_media()
    p.appliquer([{"op": "add_clip", "media": "m1", "start": 0, "duration": 4, "id": "a"},
                 {"op": "add_clip", "media": "m1", "start": 4, "in": 8, "duration": 4, "id": "b"},
                 {"op": "add_clip", "media": "m1", "start": 10, "duration": 2, "id": "c"}])
    p.appliquer([{"op": "set_transition", "clip": "b", "type": "fondu_enchaine", "duration": 1.0}])
    sp = spans(p.doc)
    assert sp["a"] == [0, 4.5] and sp["b"] == [3.5, 8] and sp["c"] == [10, 12]
    with pytest.raises(OpError, match="Aucun clip ne finit juste avant"):
        p.appliquer([{"op": "set_transition", "clip": "c", "type": "volet"}])
    with pytest.raises(OpError, match="trop longue"):
        p.appliquer([{"op": "set_transition", "clip": "b", "type": "volet", "duration": 5}])
    with pytest.raises(OpError, match="n'est pas une transition"):
        p.appliquer([{"op": "set_transition", "clip": "b", "type": "blur"}])
    with pytest.raises(OpError, match="est une transition"):
        p.appliquer([{"op": "add_effect", "clip": "b", "effect": "iris"}])
    # scinder le clip entrant : seule sa moitié gauche garde la transition
    (r,), _ = p.appliquer([{"op": "split_clip", "clip": "b", "at": 6}])
    assert "trans_in" in p.doc["clips"]["b"] and "trans_in" not in p.doc["clips"][r]
    # transition sur mesure écrite par l'IA
    p.appliquer([{"op": "define_effect", "name": "spirale", "kind": "transition",
                  "glsl": "vec4 effect(vec2 uv){ return mix(below(uv), src(uv), p_progress); }"},
                 {"op": "set_transition", "clip": r, "type": "spirale", "duration": 0.5}])
    assert p.doc["effects"]["spirale"]["blend"] == "replace" and "progress" in p.doc["effects"]["spirale"]["params"]
    p.appliquer([{"op": "set_transition", "clip": r, "type": None}])
    assert "trans_in" not in p.doc["clips"][r]


def test_images_ia_formats_calcul_et_placement():
    from moteur import images
    from moteur.gpu import Gpu
    p = projet_avec_media()
    doc = p.doc
    # formats : multiples de 8, aire ~ constante, rapport respecté
    for a in images.FORMATS:
        w, h = images.taille(doc, a)
        assert w % 8 == 0 and h % 8 == 0 and abs(w * h - 912 * 512) / (912 * 512) < 0.05
    assert images.taille(doc, "sequence") == (912, 512)
    w, h = images.taille(doc, "9:16")
    assert h > w and abs(w / h - 9 / 16) < 0.01
    with pytest.raises(ValueError):
        images.taille(doc, "2:1")
    assert images.prompt_complet("a desk", "photo").startswith("a desk, professional photograph")
    assert images.prompt_complet("a desk", "aucun") == "a desk"
    with pytest.raises(ValueError):
        images.prompt_complet("  ", "photo")
    # calcul : la mesure décide en auto (carte qui chauffe → processeur)
    g = [Gpu("nvidia", "GTX 1650", 4096)]
    perf = {"cuda": {"v": 229}, "cpu": {"v": 31}}
    assert images.choisir_calcul("auto", g, perf) == "cpu"
    assert images.choisir_calcul("auto", g, {"cuda": {"v": 20}, "cpu": {"v": 31}}) == "cuda"
    assert images.choisir_calcul("auto", g, {}) == "cuda"
    assert images.choisir_calcul("cuda", g, perf) == "cuda"
    assert images.choisir_calcul("auto", [Gpu("nvidia", "GT 710", 2048)], {}) == "cpu"
    assert images.estimation(perf, "cpu", 912, 512) == pytest.approx(31 * 4 * 0.466944 + 2)
    # placement : V2 libre → dessus, avec zoom lent ; les ops passent la validation
    p.appliquer([{"op": "add_media", "media": {"id": "g1", "name": "ia", "path": "ia.png", "kind": "image",
                                               "duration": 0.0, "width": 912, "height": 512, "proxy": None}},
                 {"op": "add_media", "media": {"id": "g2", "name": "ia2", "path": "ia2.png", "kind": "image",
                                               "duration": 0.0, "width": 912, "height": 512, "proxy": None}}],
                internal=True)
    p.appliquer([{"op": "add_clip", "media": "m1", "start": 0, "duration": 20}])
    ops = images.ops_placer(doc, ["g1", "g2"], 3.0, 4.0)
    res, _ = p.appliquer(ops)
    clips = [doc["clips"][r] for o, r in zip(ops, res) if o["op"] == "add_clip"]
    assert [(c["track"], c["start"], c["duration"]) for c in clips] == [("V2", 3.0, 4.0), ("V2", 7.0, 4.0)]
    assert clips[0]["keyframes"]["scale"][0]["v"] == 1.02 and clips[0]["keyframes"]["scale"][-1]["v"] == 1.1
    # plus de piste libre : nouvelle piste insérée SOUS les sous-titres
    p.appliquer([{"op": "add_track", "kind": "video", "name": "Sous-titres", "role": "subtitles"}])
    for t in ("V2", "V3"):
        p.appliquer([{"op": "add_clip", "type": "solid", "track": t, "start": 0, "duration": 20, "overwrite": True}])
    ops = images.ops_placer(doc, ["g1"], 5.0, 2.0, zoom=False)
    res, _ = p.appliquer(ops)
    ids = [t["id"] for t in doc["tracks"]]
    new = doc["clips"][res[1]]["track"]
    subs = next(t["id"] for t in doc["tracks"] if t.get("role") == "subtitles")
    assert ids.index(new) == ids.index(subs) - 1 and not doc["clips"][res[1]]["keyframes"]


def test_proxys_interrompus_repris_au_demarrage(tmp_path):
    from moteur.medias import nettoyer_partiels, proxys_a_reprendre
    src = tmp_path / "src.mp4"
    src.write_bytes(b"x")
    (tmp_path / "proxies").mkdir()
    (tmp_path / "proxies" / "m1.mp4").write_bytes(b"fini")          # fini, mais non enregistré
    (tmp_path / "proxies" / "m2.part.mp4").write_bytes(b"moitie")    # interrompu en cours d'écriture
    (tmp_path / "proxies" / "m5.mp4").write_bytes(b"ok")
    base = {"path": str(src), "kind": "video", "browser_ok": True}
    doc = {"media": {
        "m1": {**base, "proxy": None},                                 # à relier
        "m2": {**base, "proxy": None},                                 # à refaire
        "m3": {**base, "proxy": "proxies/m3.mp4"},                     # noté mais fichier disparu : à refaire
        "m4": {**base, "proxy": None, "proxy_error": "codec"},         # échec connu : on ne boucle pas
        "m5": {**base, "proxy": "proxies/m5.mp4"},                     # sain
        "m6": {**base, "kind": "audio", "proxy": None},                # son lisible tel quel
        "m7": {**base, "kind": "audio", "browser_ok": False, "proxy": None},
        "m8": {**base, "path": str(tmp_path / "absent.mp4"), "proxy": None},  # source introuvable
        "m9": {**base, "kind": "image", "proxy": None},
    }}
    a_relier, a_refaire = proxys_a_reprendre(doc, tmp_path)
    assert {k: v.replace("\\", "/") for k, v in a_relier.items()} == {"m1": "proxies/m1.mp4"}
    assert sorted(a_refaire) == ["m2", "m3", "m7"]
    assert nettoyer_partiels(tmp_path) == 1
    assert not (tmp_path / "proxies" / "m2.part.mp4").exists()


def test_supprimer_un_media():
    p = projet_avec_media()
    p.appliquer([{"op": "add_clip", "media": "m1", "track": "V1", "start": 0, "duration": 2},
                 {"op": "add_clip", "media": "m1", "track": "V1", "start": 5, "duration": 2}])
    with pytest.raises(OpError, match="utilisé par 2 clip"):
        p.appliquer([{"op": "delete_media", "media": "m1"}])
    assert "m1" in p.doc["media"] and len(p.doc["clips"]) == 2        # refus : rien n'a bougé
    p.appliquer([{"op": "delete_media", "media": "m1", "with_clips": True}])
    assert p.doc["media"] == {} and p.doc["clips"] == {}
    p.annuler()                                                       # un seul Ctrl+Z rend tout
    assert "m1" in p.doc["media"] and len(p.doc["clips"]) == 2
    with pytest.raises(OpError, match="inconnu"):
        p.appliquer([{"op": "delete_media", "media": "m9"}])


def test_regenerer_garde_les_sous_titres_retouches():
    from moteur import sous_titres as st
    mots = [{"w": w, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, w in enumerate(
        "Le paradoxe de la crème solaire. Plus de protection. Plus de cancers. Voici l'enquête.".split())]
    caps = st.decouper(mots, max_chars=18, max_lines=1)
    p = Projet()
    p.appliquer(st.ops(p.doc, caps, "classique"))
    tr = st.piste(p.doc)["id"]
    subs = sorted((c for c in p.doc["clips"].values() if c["track"] == tr), key=lambda c: c["start"])
    assert all(not st.retouche(c) for c in subs)
    corrige, recale = subs[0]["id"], subs[2]["id"]
    duree_recalee = subs[2]["duration"] - 0.1
    p.appliquer([{"op": "update_clip", "clip": corrige, "text": {"content": "Le paradoxe (corrigé)"}},
                 {"op": "update_clip", "clip": recale, "duration": duree_recalee}])
    ops = st.ops(p.doc, caps, "shorts")
    assert ops.gardes == 2
    p.appliquer(ops)
    restants = [c for c in p.doc["clips"].values() if c["track"] == tr]
    textes = [c["text"]["content"] for c in restants]
    assert "Le paradoxe (corrigé)" in textes                         # la correction survit
    assert p.doc["clips"][recale]["duration"] == pytest.approx(duree_recalee)   # le recalage survit
    assert len(restants) == len(caps)                                 # rien posé par-dessus
    ordre = sorted(restants, key=lambda c: c["start"])
    assert all(a["start"] + a["duration"] <= b["start"] + 1e-6 for a, b in zip(ordre, ordre[1:]))
    p.appliquer(st.ops(p.doc, caps, "classique", keep_edited=False))  # écraser, si demandé
    assert "Le paradoxe (corrigé)" not in [c["text"]["content"] for c in p.doc["clips"].values()]


def test_supprimer_un_effet_sur_mesure_utilise():
    p = projet_avec_media()
    p.appliquer([{"op": "define_effect", "name": "teinte", "glsl": "vec4 effect(vec2 uv){ return src(uv); }"},
                 {"op": "add_clip", "media": "m1", "track": "V1", "start": 0, "duration": 2}])
    cid = next(iter(p.doc["clips"]))
    (fx,), _ = p.appliquer([{"op": "add_effect", "clip": cid, "effect": "teinte"}])
    with pytest.raises(OpError, match="encore utilisé"):
        p.appliquer([{"op": "delete_effect_def", "name": "teinte"}])
    # Ce que fait le menu du panneau Effets : retirer les instances puis la définition, en un lot.
    p.appliquer([{"op": "remove_effect", "clip": cid, "fx": fx}, {"op": "delete_effect_def", "name": "teinte"}])
    assert "teinte" not in p.doc["effects"] and p.doc["clips"][cid]["effects"] == []


def test_sous_titres_d_avant_les_empreintes_remplaces():
    from moteur import sous_titres as st
    mots = [{"w": w, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, w in enumerate("Un deux trois quatre cinq six".split())]
    caps = st.decouper(mots, max_chars=10, max_lines=1)
    p = Projet()
    anciens = [{k: v for k, v in o.items() if k != "auto"} for o in st.ops(p.doc, caps)]   # comme une version d'avant
    p.appliquer(anciens)
    ops = st.ops(p.doc, caps, "shorts")
    assert ops.gardes == 0 and sum(o["op"] == "delete_clip" for o in ops) == len(caps)
