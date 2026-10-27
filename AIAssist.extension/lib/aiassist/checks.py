# -*- coding: utf-8 -*-
"""Model checks exposed as assistant tools: level assignment, clashes, untagged.

Geometry is computed here, deterministically; the LLM only calls these tools.
"""
import clr  # noqa: F401
clr.AddReference("RevitAPI")
from System.Collections.Generic import List
from Autodesk.Revit.DB import (
    BoundingBoxIntersectsFilter, BuiltInParameter, Color,
    ElementId, ElementIntersectsElementFilter, FilteredElementCollector,
    IndependentTag, Level, Outline, OverrideGraphicSettings, StorageType,
    Transaction, UnitTypeId, UnitUtils)

from aiassist import tools as T
from aiassist.tools import (_confirm, _describe, _find_category, _id_int,
                            _param, _param_names, _resolve_ids, tool,
                            unicode_lower)

MAX_REPORT = 40
_highlighted = []  # (view id int, element id int) overridden by highlight tools


# ---------------------------------------------------------------- helpers

def _mm(doc, feet):
    return round(UnitUtils.ConvertFromInternalUnits(feet, UnitTypeId.Millimeters), 1)


def _feet(mm):
    return UnitUtils.ConvertToInternalUnits(float(mm), UnitTypeId.Millimeters)


def _names(doc, el):
    type_el = doc.GetElement(el.GetTypeId())
    if type_el is None:
        return u""
    family = getattr(type_el, "FamilyName", u"") or u""
    return u"%s %s" % (family, type_el.Name)


def _elements(uiapp, categories, scope, name_contains=None):
    """Elements of the given categories in scope: selected | active_view | project."""
    uidoc = uiapp.ActiveUIDocument
    doc = uidoc.Document
    cats = []
    for name in categories or []:
        cat = _find_category(doc, name)
        if cat is None:
            raise ValueError(u"Категория не найдена: %s" % name)
        cats.append(cat)
    cat_ids = [_id_int(c.Id) for c in cats]

    found = []
    if scope == "selected":
        for eid in uidoc.Selection.GetElementIds():
            el = doc.GetElement(eid)
            if el is not None and el.Category is not None and (
                    not cat_ids or _id_int(el.Category.Id) in cat_ids):
                found.append(el)
    else:
        for cat in cats:
            if scope == "active_view":
                col = FilteredElementCollector(doc, doc.ActiveView.Id)
            else:
                col = FilteredElementCollector(doc)
            found.extend(col.OfCategoryId(cat.Id).WhereElementIsNotElementType())

    if name_contains:
        needle = unicode_lower(name_contains)
        found = [e for e in found if needle in unicode_lower(_names(doc, e))]
    return found


def _levels(doc):
    return sorted(FilteredElementCollector(doc).OfClass(Level), key=lambda l: l.Elevation)


def _level_of(doc, el):
    lid = getattr(el, "LevelId", None)
    if lid is not None and lid != ElementId.InvalidElementId:
        return doc.GetElement(lid)
    p = el.get_Parameter(BuiltInParameter.FAMILY_LEVEL_PARAM)
    if p is not None and p.AsElementId() != ElementId.InvalidElementId:
        return doc.GetElement(p.AsElementId())
    return None


def _z_ref(el, z_mode):
    """Reference elevation (feet): insertion point, or bounding-box centre."""
    loc = el.Location
    if z_mode != "bbox_center":
        if hasattr(loc, "Point"):
            return loc.Point.Z
        if hasattr(loc, "Curve"):
            c = loc.Curve
            return (c.GetEndPoint(0).Z + c.GetEndPoint(1).Z) / 2.0
    bb = el.get_BoundingBox(None)
    if bb is None:
        return None
    return (bb.Min.Z + bb.Max.Z) / 2.0


def _expected_level(levels, z, tol):
    """Highest level whose elevation is at or below z (within tolerance)."""
    best = None
    for lv in levels:
        if lv.Elevation <= z + tol:
            best = lv
    return best


def _highlight(doc, view, ids, rgb):
    ogs = OverrideGraphicSettings()
    ogs.SetProjectionLineColor(Color(rgb[0], rgb[1], rgb[2]))
    ogs.SetProjectionLineWeight(8)
    ogs.SetHalftone(False)
    done = 0
    t = Transaction(doc, u"AI Assist: подсветка")
    t.Start()
    try:
        for i in ids:
            try:
                view.SetElementOverrides(ElementId(i), ogs)
                _highlighted.append((_id_int(view.Id), i))
                done += 1
            except Exception:
                pass  # not overridable in this view
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    return done


def _select(uidoc, ids):
    uidoc.Selection.SetElementIds(List[ElementId]([ElementId(i) for i in ids]))


_CATEGORIES = {"type": "array", "items": {"type": "string"},
               "description": "Имена категорий, напр. [\"Обобщенные модели\"]"}
_SCOPE = {"type": "string", "enum": ["selected", "active_view", "project"],
          "description": "Где искать: выделенные / активный вид / весь проект"}


# ------------------------------------------------------------------ tools

@tool("list_levels", "Уровни проекта с отметками в мм.", {})
def list_levels(uiapp):
    doc = uiapp.ActiveUIDocument.Document
    return {"levels": [{"name": l.Name, "elevation_mm": _mm(doc, l.Elevation)}
                       for l in _levels(doc)]}


@tool("check_level_assignment",
      "Проверить привязку элементов (например отверстий) к уровню: сравнивает "
      "реальную отметку элемента с отметками уровней и находит те, что привязаны "
      "к неподходящему уровню (например с отрицательным смещением от вышележащего "
      "уровня, хотя ниже есть другой уровень). Результат запоминается как "
      "ids=\"last\" для fix_level_assignment.",
      {"categories": _CATEGORIES, "scope": _SCOPE,
       "name_contains": {"type": "string",
                         "description": "Фильтр по имени семейства/типа, напр. «Отверстие»"},
       "tolerance_mm": {"type": "number", "description": "Допуск, по умолчанию 5 мм"},
       "z_mode": {"type": "string", "enum": ["origin", "bbox_center"],
                  "description": "Какую отметку сравнивать: точка вставки (по умолчанию) или центр габаритов"}},
      required=["categories"])
def check_level_assignment(uiapp, categories, scope="active_view",
                           name_contains=None, tolerance_mm=5, z_mode="origin"):
    doc = uiapp.ActiveUIDocument.Document
    levels = _levels(doc)
    tol = _feet(tolerance_mm)
    elements = _elements(uiapp, categories, scope, name_contains)

    bad, checked, skipped = [], 0, 0
    for el in elements:
        lvl, z = _level_of(doc, el), _z_ref(el, z_mode)
        if lvl is None or z is None:
            skipped += 1
            continue
        checked += 1
        exp = _expected_level(levels, z, tol)
        if exp is None or exp.Id == lvl.Id:
            continue
        bad.append({
            "id": _id_int(el.Id), "name": _names(doc, el),
            "current_level": lvl.Name, "current_offset_mm": _mm(doc, z - lvl.Elevation),
            "expected_level": exp.Name, "expected_offset_mm": _mm(doc, z - exp.Elevation)})

    T._last_found = [b["id"] for b in bad]
    return {"checked": checked, "skipped_no_level_or_position": skipped,
            "mismatched": len(bad), "items": bad[:MAX_REPORT],
            "note": u"Несоответствующие id сохранены как ids=\"last\""}


@tool("fix_level_assignment",
      "Перепривязать элементы к ожидаемому уровню, сохранив их положение в "
      "пространстве (меняет уровень и смещение). Запрашивает подтверждение. "
      "Параметры вроде «Этаж» могут потребовать пересчёта отдельно.",
      {"ids": {"type": "string", "description": "\"last\" или id через запятую"},
       "z_mode": {"type": "string", "enum": ["origin", "bbox_center"]},
       "tolerance_mm": {"type": "number"}},
      required=["ids"])
def fix_level_assignment(uiapp, ids, z_mode="origin", tolerance_mm=5):
    doc = uiapp.ActiveUIDocument.Document
    ids = _resolve_ids(ids)
    if not ids:
        return {"error": "Пустой список элементов"}
    if not _confirm(u"Перепривязка к уровню",
                    u"Перепривязать %d элементов к подходящим уровням (положение "
                    u"сохраняется)?" % len(ids)):
        return {"cancelled": True}

    levels, tol = _levels(doc), _feet(tolerance_mm)
    fixed, skipped, moved = 0, [], []
    t = Transaction(doc, u"AI Assist: привязка к уровню")
    t.Start()
    try:
        for i in ids:
            el = doc.GetElement(ElementId(i))
            z = _z_ref(el, z_mode) if el is not None else None
            lvl_p = el.get_Parameter(BuiltInParameter.FAMILY_LEVEL_PARAM) if el else None
            off_p = el.get_Parameter(BuiltInParameter.INSTANCE_ELEVATION_PARAM) if el else None
            exp = _expected_level(levels, z, tol) if z is not None else None
            if (exp is None or lvl_p is None or off_p is None
                    or lvl_p.IsReadOnly or off_p.IsReadOnly):
                skipped.append(i)
                continue
            lvl_p.Set(exp.Id)
            off_p.Set(z - exp.Elevation)
            doc.Regenerate()
            z_after = _z_ref(el, z_mode)
            if z_after is not None and abs(z_after - z) > tol:
                moved.append(i)
            fixed += 1
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    return {"fixed": fixed, "skipped": len(skipped), "skipped_sample": skipped[:10],
            "position_changed_check_these": moved[:10]}


@tool("find_clashes",
      "Найти пересечения элементов A (выделенные / на виде / в проекте) с "
      "элементами категорий B в текущей модели (не в связях). Можно подсветить "
      "красным на активном виде и выделить найденные элементы. Например: после "
      "постановки стены проверить пересечение с балками.",
      {"categories_a": _CATEGORIES, "scope_a": _SCOPE,
       "categories_b": {"type": "array", "items": {"type": "string"},
                        "description": "С чем проверять пересечения, напр. [\"Каркас несущий\"]"},
       "name_contains_a": {"type": "string"},
       "highlight": {"type": "boolean",
                     "description": "Подсветить и выделить найденное (требует подтверждения)"}},
      required=["categories_a", "categories_b"])
def find_clashes(uiapp, categories_a, categories_b, scope_a="selected",
                 name_contains_a=None, highlight=False):
    uidoc = uiapp.ActiveUIDocument
    doc = uidoc.Document
    a_list = _elements(uiapp, categories_a, scope_a, name_contains_a)
    truncated = len(a_list) > 300
    a_list = a_list[:300]
    b_cats = []
    for name in categories_b:
        cat = _find_category(doc, name)
        if cat is None:
            return {"error": u"Категория не найдена: %s" % name}
        b_cats.append(cat)

    pairs, failed = [], 0
    for a in a_list:
        bb = a.get_BoundingBox(None)
        if bb is None:
            failed += 1
            continue
        quick = BoundingBoxIntersectsFilter(Outline(bb.Min, bb.Max))
        for cat in b_cats:
            try:
                col = (FilteredElementCollector(doc).OfCategoryId(cat.Id)
                       .WhereElementIsNotElementType().WherePasses(quick)
                       .WherePasses(ElementIntersectsElementFilter(a)))
                for b in col:
                    if b.Id != a.Id:
                        pairs.append((a, b))
            except Exception:
                failed += 1

    ids = sorted(set([_id_int(a.Id) for a, _ in pairs] + [_id_int(b.Id) for _, b in pairs]))
    T._last_found = ids
    out = {"elements_a_checked": len(a_list), "truncated_to_300": truncated,
           "no_geometry_or_failed": failed, "clash_pairs": len(pairs),
           "pairs": [{"a": _describe(doc, a), "b": _describe(doc, b)}
                     for a, b in pairs[:MAX_REPORT]]}
    if highlight and ids:
        if _confirm(u"Подсветка коллизий",
                    u"Подсветить %d элементов красным на виде «%s» и выделить их?"
                    % (len(ids), doc.ActiveView.Name)):
            out["highlighted"] = _highlight(doc, doc.ActiveView, ids, (255, 0, 0))
            _select(uidoc, ids)
            out["note"] = (u"Элементы вне диапазона вида или скрытые могут не "
                           u"подсветиться; все id сохранены как ids=\"last\"")
        else:
            out["highlight_cancelled"] = True
    return out


@tool("find_untagged",
      "Найти на активном виде элементы без марки: нет IndependentTag (режим tag) "
      "или пустой параметр «Марка» (режим mark_param). Подсвечивает и выделяет "
      "найденные. Например: перемычки, отверстия, стены на кладочном плане.",
      {"categories": _CATEGORIES,
       "name_contains": {"type": "string"},
       "mode": {"type": "string", "enum": ["tag", "mark_param"],
                "description": "tag — нет марки-выноски на виде (по умолчанию); mark_param — пуст параметр «Марка»"},
       "highlight": {"type": "boolean", "description": "Подсветить оранжевым (по умолчанию да)"}},
      required=["categories"])
def find_untagged(uiapp, categories, name_contains=None, mode="tag", highlight=True):
    uidoc = uiapp.ActiveUIDocument
    doc = uidoc.Document
    view = doc.ActiveView
    elements = _elements(uiapp, categories, "active_view", name_contains)

    if mode == "mark_param":
        missing = []
        for el in elements:
            p = el.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
            if p is not None and not (p.AsString() or u"").strip():
                missing.append(el)
    else:
        tagged = set()
        for tag in FilteredElementCollector(doc, view.Id).OfClass(IndependentTag):
            try:
                for eid in tag.GetTaggedLocalElementIds():
                    tagged.add(_id_int(eid))
            except Exception:
                pass
        missing = [e for e in elements if _id_int(e.Id) not in tagged]

    ids = [_id_int(e.Id) for e in missing]
    T._last_found = ids
    by_type = {}
    for e in missing:
        key = _names(doc, e)
        by_type[key] = by_type.get(key, 0) + 1
    out = {"view": view.Name, "checked": len(elements), "untagged": len(missing),
           "by_type": [{"type": k, "count": v} for k, v in
                       sorted(by_type.items(), key=lambda kv: -kv[1])[:MAX_REPORT]],
           "note": u"Все id сохранены как ids=\"last\""}
    if highlight and ids:
        if _confirm(u"Подсветка немаркированных",
                    u"Подсветить %d элементов оранжевым на виде «%s» и выделить их?"
                    % (len(ids), view.Name)):
            out["highlighted"] = _highlight(doc, view, ids, (255, 128, 0))
            _select(uidoc, ids)
        else:
            out["highlight_cancelled"] = True
    return out


@tool("clear_highlights",
      "Снять подсветку, сделанную ассистентом (find_clashes, find_untagged), на "
      "текущем виде.", {})
def clear_highlights(uiapp):
    doc = uiapp.ActiveUIDocument.Document
    view = doc.ActiveView
    view_id = _id_int(view.Id)
    mine = [i for v, i in _highlighted if v == view_id]
    if not mine:
        return {"cleared": 0}
    t = Transaction(doc, u"AI Assist: снять подсветку")
    t.Start()
    try:
        for i in mine:
            try:
                view.SetElementOverrides(ElementId(i), OverrideGraphicSettings())
            except Exception:
                pass
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    _highlighted[:] = [(v, i) for v, i in _highlighted if v != view_id]
    return {"cleared": len(mine)}
