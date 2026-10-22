# gerberHelper

[Русский](README.md) · **English**

A helper for moving a board from Cadence Allegro to Altium. The fabrication package
(Gerber and drill files) is loaded into CAMtastic and exported to PCB; this tool prepares
the data before the import and repairs what CAMtastic got wrong afterwards. The reference
is always the fabrication data itself: everything the tool changes is taken from the
Gerber and drill files.

<p align="center"><img src="docs/img/drill.png" width="760" alt="The 1. Drill tab"></p>

## What it fixes

- **Allegro drill files (`.drl`) do not import into CAMtastic.** The tool rewrites them as
  Excellon in the layout Altium expects. Units and coordinates are not converted, repeat
  codes (`Rnn`) are expanded into explicit holes, tools are split into plated and
  non-plated. Both flavours of Allegro output are supported: tool change by `M00`, and
  by `Tnn` (`TOOL-SELECT YES`).
- **Via layers are wrong after the export to PCB.** For some blind and buried vias the
  layer pair comes out as a through hole (TOP–BOTTOM). The tool sets `STARTLAYER` /
  `ENDLAYER` from the layer pairs of the drill files.
- **Rotated pads lose their shape.** Ovals and squares at 45° arrive as upright
  rectangles. The tool restores `SHAPE`, `XSIZE`, `YSIZE` and `ROTATION` from the Gerber
  apertures, including `%AM` macros.
- **Per-layer via diameters.** For Full Stack vias the size on every layer is taken from
  the Gerber.
- **Empty net-less polygons** (`No Net`) are removed, and the references to the remaining
  ones are renumbered.
- **Polygons turned into hatching made of tracks.** This happens with Gerbers produced by
  CAM350 and similar tools: the pour is stored as thousands of overlapping strokes, so the
  PCB receives thousands of tracks instead of polygons. The tool merges the strokes into an
  outline and writes it as a `Polygon` of the same net (see below).
- **Holes without pads go missing** (mounting and other holes with no copper around them in
  the Gerber, so CAMtastic has nothing to build a pad from). The tool compares the board with
  the drill file and adds the missing ones.
- **Layer naming in Gerber files differs from export to export.** Detection uses masks from
  `layer_rules.json`, and layers can also be assigned by hand in the window.

## Workflow

1. Put the Gerber files (`*.art`) and drill files (`*.drl`, preferably with `nc_param.txt`)
   in one folder.
2. Tab "1. Сверловка" (Drill) → "Конвертировать" (Convert).
3. In CAMtastic: import the Gerbers and the resulting `.TXT` files, set up layers and drill
   pairs, extract the netlist, export to PCB.
4. In Altium: vias that arrived as pads are turned back into vias with a mass selection;
   the tool does not do this. Via diameters are usually easier to set to one value by hand.
5. `File → Save As → PCB ASCII`, close Altium, tab "2. Плата" (Board) → "Анализ" (Analyze)
   → pick the operations → "Применить" (Apply).
6. Open the result in Altium.

Layer pairs are taken from the drill file names of the form `<name>-<from>-<to>.drl`, the
way Allegro names them: `board-1-4.drl` covers layers 1 to 4.

The tool brings the board in line with the Gerbers, so manual edits go **after** its run.
Running it again over a board you have already adjusted by hand will return those spots to
what the Gerbers say and report it.

## Install and run

```bash
git clone https://github.com/Gimran/gerberHelper.git
cd gerberHelper
pip install customtkinter
```

`customtkinter` is needed only by the window; the core and the command line use the
standard library only, except the hatching operation, which also needs `shapely`
(`pip install shapely`). Tested on Windows 11 and Python 3.14; it has not been run on older
Python versions.

| How to start | What it is |
|---|---|
| `Allegro-Altium.pyw` | double-click from Explorer: the `pyw.exe` launcher runs without a console and finds Python by itself |
| `allegro2altium.bat` | fallback: checks that Python and `customtkinter` are present and says what is missing |
| `python gui.py` | the same window from a console |

## The window

<p align="center"><img src="docs/img/layers.png" width="760" alt="The Layers tab"></p>

The interface is in Russian.

- **"1. Сверловка" (Drill)**: the `.drl` files found, with layer pairs, tools and hole
  counts, and the Convert button. The output goes to a subfolder of the project
  (`drill_altium` by default).
- **"2. Плата" (Board)**: pick an ASCII PcbDoc, tick the operations, Analyze and Apply.
- **"Слои" (Layers)**: how the Gerbers map onto the layer stack. Detection reads the
  `G04 Layer: ETCH/<name>` record, or the file name when there is none. Anything detected
  wrongly is assigned by hand with a drop-down, and the choice is remembered for the
  folder. The masks live in `layer_rules.json` and can be edited without touching the code
  (the "Маски автоопределения" button). The stack depth comes from the drill pairs and can
  be set by hand.

## Board operations

| Operation | What it does | Default |
|---|---|---|
| Via layers | `STARTLAYER` / `ENDLAYER` from the drill pairs | on |
| Pad shape and rotation | `SHAPE`, `XSIZE`, `YSIZE`, `ROTATION` from the Gerber apertures | on |
| Via diameters per layer | per-layer sizes for Full Stack vias (`PADMODE=2`); `DIAMETER` is set to the maximum, as Altium itself does | off |
| Hatching tracks to polygons | merges overlapping strokes of one net into an outline, writes a `Polygon` and deletes the strokes; significant cut-outs in the pour are kept by splitting; needs `shapely` | off |
| Missing holes | for drill-file holes with neither a pad nor a via on the board, adds a net-less `Pad` the size of the hole; plating comes from the drill file, but holes from a given diameter up (the "неметаллизир. от, мм" field in the window, `--holes-npth-over` on the command line) are made non-plated; the copper ring is set with `--holes-ring`; holes with a wrong diameter are only listed | off |
| Silkscreen: cut-outs and regions | polygons nested inside other silkscreen polygons (the counters of letters such as O or A), which CAMtastic fills in, become inner contours of a `Region`; all silkscreen polygons are rewritten as regions, because a Region is not recomputed on Repour and does not slow it down (`--silk-keep-polygons`: only those with cut-outs, the rest get "Remove Islands Less Than" cleared); needs `shapely` | off |
| Remove net-less polygons | polygon pours without a net on copper layers; silkscreen polygons are left alone | on |

Extra checkboxes: zero the via size on layers where the Gerber has no pad, and also remove
net-less polygons on silkscreen layers.

## Command line

```bash
python cli.py info --folder <folder>                     what was found in the folder
python cli.py run --all --folder <folder>                analysis, writes nothing
python cli.py run --all --folder <folder> --apply       write the changes
python cli.py run drill --folder <folder> --out <dir>   drill conversion only
```

Steps: `drill`, `via_layers`, `pads`, `via_sizes`, `polygons`. The options `--pcb`,
`--zero-missing`, `--include-silk` and `-v` are described in `python cli.py -h`. Manual
layer assignment is available in the window only.

## How the tool treats the board file

- **Nothing is written without Apply** (without `--apply` on the command line). Analysis
  computes everything and shows a report.
- **A copy `name.PcbDoc.bakN` is made before writing**; the number grows and older copies
  are never overwritten.
- **Only the fields that need to change are edited.** The file is split into records and
  line separators and values are replaced inside the lines, so record order, line endings
  and every other byte stay as they were.
- **Integrity check.** After the operations the number of records of every type is
  compared; only what is deliberately deleted may change. On an error, for example a
  dangling reference to a removed polygon, the write is cancelled.
- **A second run over an unchanged board makes zero changes.**

## Limitations

- The PcbDoc must be in text form: `File → Save As → PCB ASCII`. Binary files are not read.
- Altium must have the file closed when the tool writes it.
- The drill format comes from `nc_param.txt`, from the `M48` header, or from
  `;FILE_FORMAT`; if none is present, inch 2:5 is assumed.
- Pads with a complex aperture (`%AM` with primitive 4, a polygon outline) are left alone
  and listed in the report.
- Layers inside a via's span where the Gerber has no pad are left as they are by default.
  The "zero" checkbox puts 0 there, but how Altium's DRC treats a zero annular ring has to
  be checked.
- A via that does not sit exactly on its hole is flagged in the report.
- The tool has been checked on two eight-layer boards from Allegro 16.6 exports. Other
  exports may need changes to the layer masks or to the code.

## Tests

```bash
python tests/regression.py
python tests/test_hatch.py
python tests/test_holes.py
python tests/test_silk.py
```

`regression.py` runs the tool on two boards and compares the result byte for byte with
reference outputs: drill conversion for both flavours of Allegro output and the full set of
PcbDoc operations. `test_hatch.py` and `test_holes.py` check hatching and missing holes on
synthetic boards and need no real data.

The real boards used for this are not in the repository (`tests/fixtures/` is in
`.gitignore`). Without them the cases in `tests/cases.json` are reported as skipped, not
failed. To test against your own data, put your exports there following the layout in
`cases.json`: a folder with `.drl` files and `nc_param.txt`, plus an `expected/` folder
with the expected `.TXT` files.

## Layout

```
Allegro-Altium.pyw    window launcher
allegro2altium.bat    launcher with diagnostics
gui.py                the window: three tabs
cli.py                the same operations from the command line
layer_rules.json      layer detection masks
core/
  gerber.py           RS-274X reader: apertures, %AM macros, flashes
  excellon.py         reads Allegro drill files, writes the Altium layout
  pcbdoc.py           ASCII PcbDoc: field edits without shifting any other byte
  hatch.py            hatching tracks to polygons (shapely)
  silk.py             silkscreen cut-outs (shapely)
  layers.py           applies the detection masks
  project.py          what is in the folder, the stack, the layer mapping
  ops.py              drill and board operations
  runner.py           operation order, integrity check, writing
tests/                regression with reference outputs
```

`settings.json` (last folder, checkboxes, manual layer assignments) is created on the
first run and is not committed.

## License

[MIT](LICENSE)
