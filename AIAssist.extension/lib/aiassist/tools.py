# -*- coding: utf-8 -*-
"""Tools exposed to the LLM. Each runs on Revit's main thread: fn(uiapp, **args).

Read tools run immediately. Write tools ask the user to confirm and run in a
single Transaction.
"""
import clr  # noqa: F401
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
from System.Collections.Generic import List
from Autodesk.Revit.DB import (
    ElementId, FilteredElementCollector, Grid, DatumEnds, StorageType,
    Transaction, UnitUtils)
from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons, TaskDialogResult

TOOLS = {}
_last_found = []


def tool(name, description, properties, required=()):
    def deco(fn):
        TOOLS[name] = {
            "fn": fn,
            "spec": {
                "name": name,
                "description": description,
                "input_schema": {
                    "type": "object",
                    "properties": properties,
                    "required": list(required),
                },
            },
        }
        return fn
    return deco


def specs():
    return [t["spec"] for t in TOOLS.values()]


# ---------------------------------------------------------------- helpers

def _id_int(eid):
    try:
        return int(eid.Value)  # Revit 2024+
    except AttributeError:
        return eid.IntegerValue


def _find_category(doc, name):
    wanted = name.strip().lower()
    for cat in doc.Settings.Categories:
        if cat.Name.lower() == wanted:
            return cat
    for cat in doc.Settings.Categories:
        if wanted in cat.Name.lower():
            return cat
    return None


def _param(doc, el, name):
    p = el.LookupParameter(name)
    if p is None:
        type_el = doc.GetElement(el.GetTypeId())
        if type_el is not None:
            p = type_el.LookupParameter(name)
    return p


def _unit_id(doc, p):
    """Project display unit for measurable parameters, else None."""
    try:
        spec = p.Definition.GetDataType()
        if UnitUtils.IsMeasurableSpec(spec):
            return doc.GetUnits().GetFormatOptions(spec).GetUnitTypeId()
    except Exception:
        pass
    return None


def _value(doc, p):
    st = p.StorageType
    if st == StorageType.Double:
        unit = _unit_id(doc, p)
        if unit is not None:
            return UnitUtils.ConvertFromInternalUnits(p.AsDouble(), unit)
        return p.AsDouble()
    if st == StorageType.Integer:
        return p.AsInteger()
    if st == StorageType.String:
        return p.AsString()
    return p.AsValueString()


def _describe(doc, el):
    cat = el.Category.Name if el.Category is not None else None
    type_el = doc.GetElement(el.GetTypeId())
    return {
        "id": _id_int(el.Id),
        "category": cat,
        "type": type_el.Name if type_el is not None else None,
    }


def _resolve_ids(ids):
    if ids == "last":
        return list(_last_found)
    return [int(i) for i in ids]


def _confirm(title, text):
    dlg = TaskDialog(title)
    dlg.MainInstruction = title
    dlg.MainContent = text
    dlg.CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No
    return dlg.Show() == TaskDialogResult.Yes


def _compare(actual, op, expected):
    if actual is None:
        return False
    if op == "contains":
        return unicode_lower(expected) in unicode_lower(actual)
    if isinstance(actual, (int, float)):
        expected = float(expected)
    else:
        actual, expected = unicode_lower(actual), unicode_lower(expected)
    return {
        "eq": actual == expected, "ne": actual != expected,
        "gt": actual > expected, "gte": actual >= expected,
        "lt": actual < expected, "lte": actual <= expected,
    }[op]


def unicode_lower(v):
    try:
        return unicode(v).lower()  # noqa: F821 (IronPython 2.7)
    except NameError:
        return str(v).lower()


# ------------------------------------------------------------------ tools

@tool("get_model_summary",
      "Название проекта, активный вид и количество экземпляров по категориям.",
      {})
def get_model_summary(uiapp):
    doc = uiapp.ActiveUIDocument.Document
    counts = {}
    for el in FilteredElementCollector(doc).WhereElementIsNotElementType():
        if el.Category is not None:
            counts[el.Category.Name] = counts.get(el.Category.Name, 0) + 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:40]
    return {
        "project": doc.Title,
        "active_view": doc.ActiveView.Name,
        "categories": [{"name": n, "count": c} for n, c in top],
    }


@tool("find_elements",
      "Найти элементы категории по условиям на параметры. Числа сравниваются в "
      "единицах проекта (обычно мм / м²). Результат запоминается: далее можно "
      "передавать ids=\"last\" в select_elements и set_parameter.",
      {
          "category": {"type": "string",
                       "description": "Имя категории, напр. «Стены» или Walls"},
          "scope": {"type": "string", "enum": ["project", "active_view"],
                    "description": "По умолчанию project"},
          "filters": {
              "type": "array",
              "description": "Условия, объединяются по И",
              "items": {
                  "type": "object",
                  "properties": {
                      "parameter": {"type": "string"},
                      "op": {"type": "string",
                             "enum": ["eq", "ne", "gt", "gte", "lt", "lte",
                                      "contains"]},
                      "value": {"type": ["string", "number"]},
                  },
                  "required": ["parameter", "op", "value"],
              },
          },
      },
      required=["category"])
def find_elements(uiapp, category, scope="project", filters=None):
    global _last_found
    doc = uiapp.ActiveUIDocument.Document
    cat = _find_category(doc, category)
    if cat is None:
        return {"error": u"Категория не найдена: %s" % category}
    if scope == "active_view":
        collector = FilteredElementCollector(doc, doc.ActiveView.Id)
    else:
        collector = FilteredElementCollector(doc)
    collector = collector.OfCategoryId(cat.Id).WhereElementIsNotElementType()

    found = []
    for el in collector:
        ok = True
        for f in (filters or []):
            p = _param(doc, el, f["parameter"])
            if p is None or not _compare(_value(doc, p), f["op"], f["value"]):
                ok = False
                break
        if ok:
            found.append(el)

    _last_found = [_id_int(e.Id) for e in found]
    return {
        "category": cat.Name,
        "count": len(found),
        "sample": [_describe(doc, e) for e in found[:10]],
        "note": u"Все id сохранены как ids=\"last\"",
    }


@tool("select_elements",
      "Выделить элементы в Revit.",
      {"ids": {"description": "Список id или строка \"last\"",
               "anyOf": [{"type": "array", "items": {"type": "integer"}},
                         {"type": "string", "enum": ["last"]}]}},
      required=["ids"])
def select_elements(uiapp, ids):
    ids = _resolve_ids(ids)
    net = List[ElementId]([ElementId(i) for i in ids])
    uiapp.ActiveUIDocument.Selection.SetElementIds(net)
    return {"selected": len(ids)}


@tool("get_parameters",
      "Все параметры одного элемента (имя, значение, только чтение).",
      {"id": {"type": "integer"}}, required=["id"])
def get_parameters(uiapp, id):
    doc = uiapp.ActiveUIDocument.Document
    el = doc.GetElement(ElementId(int(id)))
    if el is None:
        return {"error": "Элемент не найден"}
    out = []
    for p in el.Parameters:
        out.append({"name": p.Definition.Name, "value": _value(doc, p),
                    "read_only": p.IsReadOnly})
    return {"id": id, "parameters": out}


@tool("set_parameter",
      "Записать значение параметра у элементов. Запрашивает подтверждение "
      "пользователя. Числа — в единицах проекта.",
      {"ids": {"description": "Список id или \"last\"",
               "anyOf": [{"type": "array", "items": {"type": "integer"}},
                         {"type": "string", "enum": ["last"]}]},
       "parameter": {"type": "string"},
       "value": {"type": ["string", "number"]}},
      required=["ids", "parameter", "value"])
def set_parameter(uiapp, ids, parameter, value):
    doc = uiapp.ActiveUIDocument.Document
    ids = _resolve_ids(ids)
    if not ids:
        return {"error": "Пустой список элементов"}
    if not _confirm(u"Изменить параметр",
                    u"Записать «%s» = %s у %d элементов?" % (parameter, value, len(ids))):
        return {"cancelled": True}

    changed, skipped = 0, []
    t = Transaction(doc, u"AI Assist: " + parameter)
    t.Start()
    try:
        for i in ids:
            el = doc.GetElement(ElementId(i))
            p = _param(doc, el, parameter) if el is not None else None
            if p is None or p.IsReadOnly:
                skipped.append(i)
                continue
            st = p.StorageType
            if st == StorageType.String:
                p.Set(u"%s" % value)
            elif st == StorageType.Integer:
                p.Set(int(float(value)))
            elif st == StorageType.Double:
                unit = _unit_id(doc, p)
                v = float(value)
                p.Set(UnitUtils.ConvertToInternalUnits(v, unit) if unit is not None else v)
            else:
                skipped.append(i)
                continue
            changed += 1
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    return {"changed": changed, "skipped": len(skipped), "skipped_sample": skipped[:10]}


@tool("set_grid_bubbles",
      "Показать/скрыть кружки осей на активном виде. end0/end1 — какой конец "
      "оси (по направлению оси); не указанный конец не меняется. Запрашивает "
      "подтверждение.",
      {"end0": {"type": "boolean"}, "end1": {"type": "boolean"}})
def set_grid_bubbles(uiapp, end0=None, end1=None):
    doc = uiapp.ActiveUIDocument.Document
    view = doc.ActiveView
    grids = list(FilteredElementCollector(doc, view.Id).OfClass(Grid))
    if not grids:
        return {"error": "На активном виде нет осей"}
    if not _confirm(u"Кружки осей",
                    u"Изменить кружки у %d осей на виде «%s»?" % (len(grids), view.Name)):
        return {"cancelled": True}

    t = Transaction(doc, u"AI Assist: кружки осей")
    t.Start()
    try:
        for g in grids:
            for flag, end in ((end0, DatumEnds.End0), (end1, DatumEnds.End1)):
                if flag is None:
                    continue
                if flag:
                    g.ShowBubbleInView(end, view)
                else:
                    g.HideBubbleInView(end, view)
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    return {"grids": len(grids), "view": view.Name}
