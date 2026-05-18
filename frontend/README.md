# MatterVis Frontend Assets

This directory contains browser-only JavaScript and CSS loaded by the Dash app.
Do not place Python service or library code here.

Dash loads these files through the explicit `assets_folder` configured in
`app/dash/factory.py`. Keep filenames stable when external browser automation
or tests depend on them.
