"""Prompt text describing the diagram DSL to language models (kept next to the renderer it documents)."""

DIAGRAM_DSL_DOC = r"""
DIAGRAM SPEC (JSON). Use it wherever a figure is needed, in a question's "diagram" or an answer's "diagram".
Every spec has "type": one of geometry | plot | chart | venn | number_line. Optional on all: "caption" (e.g. "Fig. 1"),
"width_cm" (3-14).

1) geometry - shapes on a plane using explicit coordinates. Coordinates MUST agree with the lengths/angles stated in
   the question text (draw to scale).
   {"type":"geometry",
    "points":{"A":[0,0],"B":[6,0],"C":[0,4.5]},            // names starting with "_" are invisible helper points
    "shapes":[
      {"kind":"polygon","points":["A","B","C"],"fill":false},
      {"kind":"segment","from":"A","to":"B","label":"6 cm","style":"solid|dashed|dotted","ticks":1},
      {"kind":"line","through":["A","C"]},                   // infinite line
      {"kind":"arrow","from":"A","to":"B","label":"F"},
      {"kind":"circle","center":"O","radius":3,"label":"","fill":false},
      {"kind":"ellipse","center":"O","width":6,"height":3,"angle":0},
      {"kind":"rect","from":[0,0],"to":[4,2],"label":""},
      {"kind":"arc","center":"O","radius":1,"from_deg":0,"to_deg":60,"label":"$\\theta$"},
      {"kind":"angle","at":"B","from":"A","to":"C","label":"$60^\\circ$"},
      {"kind":"right_angle","at":"A","from":"B","to":"C"},
      {"kind":"text","at":[1,1],"text":"label"}]}
   Use for triangles, circles, tangents, quadrilaterals, 3D-looking solids drawn in 2D, ray diagrams, force diagrams,
   circuits (as segments/rects), apparatus sketches, cell/organ outlines (ellipse/arc).

2) plot - Cartesian axes with functions, points, lines and shaded regions.
   {"type":"plot","xlim":[-5,5],"ylim":[-5,5],"axis_labels":["x","y"],"grid":true,
    "functions":[{"expr":"x^2-4","label":"$y=x^2-4$","style":"solid|dashed","domain":[-3,3]}],
    "points":[{"x":2,"y":0,"label":"A","open":false}],
    "lines":[{"from":[0,0],"to":[4,4],"style":"dashed","arrow":false}],
    "polygons":[{"points":[[0,0],[4,0],[2,3]],"fill":true}],        // feasible regions
    "fills":[{"expr1":"x","expr2":"x^2","domain":[0,1]}]}           // area between curves
   "expr" may use x, numbers, + - * / ^, sin cos tan asin acos atan exp ln log sqrt abs, pi, e. Nothing else.

3) chart - data displays for statistics.
   {"type":"chart","kind":"bar|line|pie|histogram|scatter","categories":["A","B"],
    "series":[{"name":"Boys","values":[3,5]}],"x_label":"","y_label":"","show_values":true}
   histogram: add "bins":[0,10,20,30] (one more edge than values) and optional "polygon":true.
   scatter: use "points":[[x,y],...] and optional "trendline":true instead of series.

4) venn - 2 or 3 sets. {"type":"venn","sets":[{"label":"A"},{"label":"B"}],
   "regions":{"A":"5","B":"7","AB":"3","none":"2"}}   // region keys are letters of the sets in order (A,B,C,AB,AC,BC,ABC) or "none"

5) number_line - {"type":"number_line","min":-5,"max":5,"step":1,"points":[{"value":2,"label":"P","open":false}],
   "intervals":[{"from":-2,"to":3,"from_closed":true,"to_closed":false}]}

Labels may contain LaTeX between single dollar signs. Keep every figure simple, uncluttered and exam-appropriate.
Never draw the answer inside a question's diagram.
""".strip()
