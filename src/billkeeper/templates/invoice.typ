// The Typst template Pandoc renders the invoice body through.
//
// This file is a copy, not a link: editing it changes this data repo's
// invoices and nothing else, and billkeeper will never write over it.
//
// $body$ is where Pandoc puts the rendered Markdown. A literal dollar sign
// has to be written $$ in this file, because Pandoc reads the template before
// Typst ever sees it.

#set page(paper: "a4", margin: 20mm)
#set text(font: ("Inter", "Helvetica Neue", "Arial"), size: 10pt)
#set par(justify: false)
#show raw: set text(font: ("JetBrains Mono", "DejaVu Sans Mono"))

$body$
