\# Multipoint Line Naming (QGIS Plugin)



\*\*Multipoint Line Naming\*\* is an automated spatial topology and attribute assignment plugin built for QGIS 3. It utilizes graph theory (`NetworkX`) and spatial indexing (`QgsSpatialIndex`) to trace network paths along vector line layers, automatically generating standardized upstream-to-downstream segment names based on connected origin and secondary node features (such as Hub-Boxes, FATs, OLTs, and Closures).



\### Key Highlights

\- \*\*Automated Shortest-Path Tracing:\*\* Dynamically builds network graphs across connected line segments and point features.

\- \*\*Disconnected Subgraph Support:\*\* Gracefully handles isolated or multi-root network branches.

\- \*\*Smart Prefix Trimming:\*\* Removes redundant site DUIDs for cleaner attribute naming.

\- \*\*Flexible UI:\*\* Pre-configured SFC \& Trenching modes, plus a custom override panel with configurable snapping tolerances.

