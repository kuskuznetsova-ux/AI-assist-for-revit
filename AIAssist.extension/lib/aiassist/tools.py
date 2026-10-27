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
from aiassist import jsonutil
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


def _param_names(doc, el):
    """Names of instance + type parameters, to help the model pick the right one."""
    names = set()
    for source in (el, doc.GetElement(el.GetTypeId())):
        if source is not None:
            for p in source.Parameters:
                names.add(p.Definition.Name)
    return sorted(names)


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
    if not isinstance(ids, (list, tuple)):
        ids = unicode_lower(ids).replace(";", ",").split(",")
    return [int(i) for i in ids if unicode_lower(i).strip()]


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
    if not isinstance(v, basestring):  # noqa: F821 (IronPython 2.7)
        v = str(v)
    return v.lower()


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
                      "value": {"type": "string", "description": "Значение (числа тоже строкой)"},
                  },
                  "required": ["parameter", "op", "value"],
              },
          },
      },
      required=["category"])
def find_elements(uiapp, category, scope="project", filters=None):
    return _do_find(uiapp.ActiveUIDocument.Document, category, scope, filters)


def _do_find(doc, category, scope, filters):
    global _last_found
    cat = _find_category(doc, category)
    if cat is None:
        return {"error": u"Категория не найдена: %s" % category}
    if scope == "active_view":
        collector = FilteredElementCollector(doc, doc.ActiveView.Id)
    else:
        collector = FilteredElementCollector(doc)
    collector = collector.OfCategoryId(cat.Id).WhereElementIsNotElementType()

    elements = list(collector)
    if elements and filters:
        sample = elements[0]
        unknown = [f["parameter"] for f in filters
                   if _param(doc, sample, f["parameter"]) is None]
        if unknown:
            return {"error": u"Параметр не найден: %s" % u", ".join(unknown),
                    "available_parameters": _param_names(doc, sample)}

    found = []
    for el in elements:
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
      {"ids": {"type": "string",
               "description": "\"last\" (результат последнего поиска) или id через запятую"}},
      required=["ids"])
def select_elements(uiapp, ids):
    ids = _resolve_ids(ids)
    net = List[ElementId]([ElementId(i) for i in ids])
    uiapp.ActiveUIDocument.Selection.SetElementIds(net)
    return {"selected": len(ids)}


@tool("sum_parameter",
      "Сумма числового параметра по элементам (например «Объем» или «Площадь») в "
      "единицах проекта. Пропускает элементы без этого параметра.",
      {"ids": {"type": "string",
               "description": "\"last\" или id через запятую"},
       "parameter": {"type": "string", "description": "Имя параметра, напр. «Объем»"}},
      required=["ids", "parameter"])
def sum_parameter(uiapp, ids, parameter):
    doc = uiapp.ActiveUIDocument.Document
    ids = _resolve_ids(ids)
    total, used, skipped, label = 0.0, 0, 0, None
    for i in ids:
        el = doc.GetElement(ElementId(i))
        p = _param(doc, el, parameter) if el is not None else None
        if p is None or p.StorageType not in (StorageType.Double, StorageType.Integer):
            skipped += 1
            continue
        total += float(_value(doc, p))
        used += 1
        if label is None and p.StorageType == StorageType.Double:
            label = p.AsValueString()  # e.g. "12.3 м³" -> shows the unit
    if used == 0 and ids:
        sample = doc.GetElement(ElementId(ids[0]))
        return {"error": u"Параметр не найден или не числовой: %s" % parameter,
                "available_parameters": _param_names(doc, sample) if sample else []}
    return {"parameter": parameter, "elements": used, "skipped": skipped,
            "total": round(total, 3), "example_value_with_unit": label}


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
      {"ids": {"type": "string",
               "description": "\"last\" (результат последнего поиска) или id через запятую"},
       "parameter": {"type": "string"},
       "value": {"type": "string", "description": "Значение (числа тоже строкой)"}},
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


# ------------------------------------------------- interactive (user picks)

OPERATORS = [
    (u"больше (>)", "gt"), (u"больше или равно (≥)", "gte"),
    (u"равно (=)", "eq"), (u"меньше (<)", "lt"),
    (u"меньше или равно (≤)", "lte"), (u"не равно (≠)", "ne"),
    (u"содержит текст", "contains"),
]


def _pick(items, title, multiple=False, button=u"Далее"):
    from pyrevit import forms
    return forms.SelectFromList.show(items, title=title, button_name=button,
                                     multiselect=multiple)


@tool("ask_user",
      "Задать пользователю уточняющий вопрос с вариантами ответа (диалог выбора "
      "в Revit) и получить выбранное. Используй, когда запрос неоднозначен.",
      {"question": {"type": "string", "description": "Заголовок диалога"},
       "options": {"type": "array", "items": {"type": "string"},
                   "description": "Варианты для выбора"},
       "multiple": {"type": "boolean",
                    "description": "Можно выбрать несколько (по умолчанию нет)"}},
      required=["question", "options"])
def ask_user(uiapp, question, options, multiple=False):
    picked = _pick(list(options), question, bool(multiple))
    if not picked:
        return {"cancelled": True}
    return {"selected": picked if multiple else [picked]}


@tool("choose_filter",
      "Интерактивный поиск: пользователь сам выбирает в диалогах параметр, условие "
      "(больше/меньше/равно…) и значение, затем выполняется поиск элементов "
      "категории. Используй, если из запроса неясно, по какому параметру или с "
      "каким условием искать (например «стены тоньше 200»). Результат "
      "запоминается как ids=\"last\".",
      {"category": {"type": "string",
                    "description": "Имя категории, напр. «Стены»"},
       "hint": {"type": "string",
                "description": "Подсказка для предвыбора параметров, напр. «толщ»"},
       "scope": {"type": "string", "enum": ["project", "active_view"]}},
      required=["category"])
def choose_filter(uiapp, category, hint=None, scope="project"):
    """Like a schedule filter: pick field, condition, value; add more conditions."""
    from pyrevit import forms
    doc = uiapp.ActiveUIDocument.Document
    cat = _find_category(doc, category)
    if cat is None:
        return {"error": u"Категория не найдена: %s" % category}

    # Parameters of the category (instance + type) from a sample of elements,
    # each with an example value so the user recognises the field.
    examples = {}
    count = 0
    for el in FilteredElementCollector(doc).OfCategoryId(cat.Id).WhereElementIsNotElementType():
        for source in (el, doc.GetElement(el.GetTypeId())):
            if source is None:
                continue
            for p in source.Parameters:
                name = jsonutil.text(p.Definition.Name)
                if name not in examples or not examples[name]:
                    examples[name] = jsonutil.text(p.AsValueString() or p.AsString() or u"")
        count += 1
        if count >= 50:
            break
    if not examples:
        return {"error": u"В проекте нет элементов категории %s" % cat.Name}

    names = sorted(examples)
    if hint:  # likely fields first, but the full list stays available
        needle = unicode_lower(hint)
        names = ([n for n in names if needle in unicode_lower(n)] +
                 [n for n in names if needle not in unicode_lower(n)])
    labels = {}
    for n in names:
        labels[(u"%s    (например: %s)" % (n, examples[n])) if examples[n] else n] = n
    ordered = [l for n in names for l, v in labels.items() if v == n]

    filters = []
    while True:
        picked = _pick(ordered, u"Условие %d: поле (%s)" % (len(filters) + 1, cat.Name))
        if not picked:
            return {"cancelled": True}
        name = labels[picked]
        label = _pick([l for l, _ in OPERATORS], u"Условие для «%s»" % name)
        if not label:
            return {"cancelled": True}
        value = forms.ask_for_string(
            prompt=u"Значение для «%s» — %s (в единицах проекта)" % (name, label),
            title=u"Значение")
        if value is None or not value.strip():
            return {"cancelled": True}
        filters.append({"parameter": name, "op": dict(OPERATORS)[label],
                        "value": value.strip()})
        more = _pick([u"Найти элементы", u"Добавить ещё условие"],
                     u"Условий: %d" % len(filters), button=u"OK")
        if more != u"Добавить ещё условие":
            break

    result = _do_find(doc, category, scope, filters)
    result["chosen_filters"] = filters
    return result


# Register tools defined in other modules.
from aiassist import checks  # noqa: E402,F401
