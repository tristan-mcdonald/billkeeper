// ==========================================================================
//  TYPEFACE — change the two lines below to change the fonts, and nothing
//  else needs touching.
//
//  Inter for text and JetBrains Mono for code are both published under the
//  SIL Open Font License 1.1, which permits use, embedding in a PDF, and
//  passing that PDF on. billkeeper does not ship the font files: Typst looks
//  for them among the fonts installed on this machine, so install the two to
//  get the intended look. The later names in each list are fallbacks, tried
//  in order when the ones before them are not installed, so an invoice still
//  renders on a machine that has neither.
// ==========================================================================

#let body-font = ("Inter", "Helvetica Neue", "Arial")
#let mono-font = ("JetBrains Mono", "DejaVu Sans Mono")

#set text(font: body-font)
#show raw: set text(font: mono-font)

// ==========================================================================
//  invoice.typ — the layout of every invoice this data repo renders.
//
//  This file is a copy, not a link: editing it changes this data repo's
//  invoices and nothing else, and billkeeper will never write over it.
//
//  Pandoc reads this file before Typst ever sees it, and takes everything
//  between dollar signs as one of its own variables. So a literal dollar
//  sign has to be written twice over — which is also why the comments below
//  write Pandoc's variables as $$name$$ rather than as $name$.
//
//  What the variables hold comes from `invoice.md` next door: $$number$$
//  (the invoice number, or the word DRAFT), $$draft$$ (true while there is
//  no number), $$kind-label$$, $$currency$$, $$created$$, $$issue-date$$,
//  $$due-date$$, $$payment-terms-days$$, $$status$$, $$issuer.name$$,
//  $$issuer.address$$, $$issuer.email$$, $$client.name$$, $$client.address$$,
//  $$client.contact$$, $$client.email$$, $$items$$ (each with description,
//  quantity, unit, unit-price and amount), $$subtotal$$, $$total$$,
//  $$payment-details$$ and $$references$$. The multi-line ones — both
//  addresses and the payment details — arrive as a list of lines, which is
//  why they are looped over below.
//
//  $$body$$ is where Pandoc puts the rendered Markdown, which by default is
//  the invoice's notes. The notes also reach here as plain metadata in
//  $$notes$$, there if you would rather place them yourself; print one or the
//  other, not both.
// ==========================================================================

#let ink = rgb("#1b1b1b")
#let muted = rgb("#6d6d6d")
#let hairline = rgb("#d5d5d5")
#let draft-ink = rgb("#b3261e")

#let thin = 0.5pt + hairline
#let thin-rule = line(length: 100%, stroke: thin)

#set document(title: [$kind-label$ $number$])

#set page(
  paper: "a4",
  margin: (x: 18mm, top: 20mm, bottom: 22mm),
  footer: context {
    set text(size: 8pt, fill: muted)
    grid(
      columns: (1fr, auto),
      align: (left + horizon, right + horizon),
      [$kind-label$ $number$$if(issuer.email)$ · $issuer.email$$endif$],
      [#counter(page).display() / #counter(page).final().first()],
    )
  },
)

#set text(size: 10pt, fill: ink, lang: "en")
#set par(justify: false, leading: 0.62em)
#show link: set text(fill: rgb("#1d4ed8"))

// Small caps are not assumed to exist in the fallback fonts, so a section
// heading is upper case and tracked out instead.
#let section-title(body) = block(
  above: 15pt,
  below: 6pt,
  text(size: 8pt, weight: "semibold", fill: muted, tracking: 0.1em, upper(body)),
)

// Headings written in the notes are styled to match the headings above them.
#show heading: it => section-title(it.body)

#let column-head(body) = text(
  size: 8pt,
  weight: "semibold",
  fill: muted,
  tracking: 0.08em,
  upper(body),
)

#let aside(body) = text(size: 9pt, fill: muted, body)

// A thematic break written in the notes. Polyfilled for Typst before 0.15,
// which has no `divider`.
#let horizontal-rule = line(start: (25%, 0%), end: (75%, 0%))
#let divider = if "divider" in std { divider } else { horizontal-rule }

$if(highlighting-definitions)$
// Styles for a fenced code block written in the notes.
$highlighting-definitions$
$endif$

// -------------------------------------------------------------- who and when

#grid(
  columns: (1fr, auto),
  column-gutter: 10mm,
  align: (left + top, right + top),
  [
    #text(size: 13pt, weight: "semibold")[$issuer.name$]
    $if(issuer.address)$
    #linebreak()
    #aside[$for(issuer.address)$$issuer.address$$sep$#linebreak()$endfor$]
    $endif$
    $if(issuer.email)$
    #linebreak()
    #aside[$issuer.email$]
    $endif$
  ],
  [
    $if(draft)$
    #text(size: 21pt, weight: "bold", fill: draft-ink, tracking: 0.08em)[DRAFT]
    #linebreak()
    #aside[$kind-label$]
    $else$
    #column-head[$kind-label$]
    #linebreak()
    #text(size: 15pt, weight: "semibold")[$number$]
    $endif$
    #v(7pt, weak: true)
    #grid(
      columns: (auto, auto),
      column-gutter: 5mm,
      row-gutter: 3pt,
      align: (left, right),
      $if(issue-date)$
      aside[Issued], text(size: 9pt)[$issue-date$],
      $else$
      aside[Raised], text(size: 9pt)[$created$],
      $endif$
      $if(due-date)$
      aside[Due], text(size: 9pt)[$due-date$],
      $endif$
    )
  ],
)

#v(13pt)
#thin-rule

#section-title[Billed to]
#text(weight: "semibold")[$client.name$]
$if(client.contact)$
#linebreak()
#aside[$client.contact$]
$endif$
$if(client.address)$
#linebreak()
#aside[$for(client.address)$$client.address$$sep$#linebreak()$endfor$]
$endif$
$if(client.email)$
#linebreak()
#aside[$client.email$]
$endif$

// ------------------------------------------------------------- what is owed

#v(16pt)
#table(
  columns: (1fr, auto, auto, auto),
  align: (left + top, right + top, right + top, right + top),
  inset: (x: 0pt, y: 7pt),
  column-gutter: 7mm,
  stroke: none,
  table.hline(stroke: thin),
  table.header(
    column-head[Description],
    column-head[Qty],
    column-head[Unit price],
    column-head[Amount],
  ),
  table.hline(stroke: thin),
  $for(items)$
  [$items.description$],
  [$items.quantity$$if(items.unit)$ $items.unit$$endif$],
  [$items.unit-price$],
  [$items.amount$],
  $endfor$
  table.hline(stroke: thin),
  table.cell(colspan: 3, align: right, aside[Subtotal]),
  text(size: 9pt)[$subtotal$],
  table.cell(colspan: 3, align: right, text(weight: "semibold")[Total]),
  text(weight: "semibold")[$total$],
  table.hline(stroke: 0.9pt + ink),
)

// --------------------------------------------------- how to pay, and the rest

$if(payment-details)$
#section-title[How to pay]
$for(payment-details)$$payment-details$$sep$#linebreak()$endfor$
$endif$

$if(references)$
#section-title[References]
$references$
$endif$

$body$
