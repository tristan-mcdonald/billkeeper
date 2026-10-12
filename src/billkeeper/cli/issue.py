"""`billkeeper issue` and `billkeeper render`: a draft becomes a document.

Issuing is the moment an invoice stops being yours to change. It takes a number
from a counter that must never show a gap, moves the file out of `drafts/` and
under its year, renders a PDF through two programs that may not be installed,
and commits all of it. Any one of those steps can fail, and a failure that left
the number drawn would put a hole in the series — which is the first thing
anyone auditing a set of invoices asks about.

So the shape of `issue` is deliberate. Everything that can be checked is checked
before the counter is touched: that the invoice is still a draft, that it has
lines to bill, that Pandoc and Typst are there to render it. Past that point the
command keeps enough to undo itself — the draft's own bytes, and the paths it
created — and the `except` puts every one of them back before re-raising. A
failed issue leaves the draft exactly as it was, the counter where it was, and
nothing committed.

`render` is the opposite kind of command: it changes nothing but a derived file
and may be run as often as the user likes. It commits only when the PDF's bytes
actually changed, which is only possible because `render_pdf` is byte-stable for
an unchanged invoice — otherwise every run would record a commit whose only
difference was the timestamp inside the PDF.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Final

import typer

from billkeeper import clock, gitrepo, render, sequence
from billkeeper.cli import get_repo
from billkeeper.config import RepoConfig
from billkeeper.errors import BillkeeperError, ImmutableInvoiceError, ValidationError
from billkeeper.models import Invoice, InvoiceStatus
from billkeeper.money import format_money
from billkeeper.storage import PDF_SUFFIX, Repo, atomic_write_bytes, invoice_id

#: What the commits made here are recorded as.
ISSUE_COMMIT_MESSAGE: Final = "Issue invoice {number}"
RENDER_COMMIT_MESSAGE: Final = "Render {ident}"

#: What `issue` reports once the invoice is out.
ISSUED_MESSAGE: Final = "Issued {number}."
TOTAL_MESSAGE: Final = "Total: {total}"
PDF_MESSAGE: Final = "PDF:   {path}"

#: What `render` says, depending on whether there was anything to do.
RENDERED_MESSAGE: Final = "Rendered {ident} to {path}."
UP_TO_DATE_MESSAGE: Final = "PDF is up to date."

#: What `issue` says about an invoice that is past drafting.
NOT_A_DRAFT_MESSAGE: Final = (
    "{ident} is {status}, and only a draft can be issued. An invoice keeps the "
    "number it was given, so a correction is a credit note or a new invoice "
    "referencing it."
)

#: What a draft that is not filed under any name it can be found by gets told.
MISFILED_MESSAGE: Final = (
    "This invoice is a draft with no draft id, so billkeeper cannot tell which "
    "file to issue. Give it a 'draft_id' matching its filename without the "
    "'.toml', or start the draft again with 'billkeeper new'."
)

#: What is said when the undo after a failed issue did not itself finish.
UNDO_FAILED_MESSAGE: Final = (
    "Issuing {ident} failed: {failure}\n"
    "Undoing it failed too: {trouble}\n"
    "Nothing has been committed, so 'git status' and 'git diff' in {root} show "
    "exactly what is left over. Put that right before issuing anything else."
)


def issue(
    ctx: typer.Context,
    ident: Annotated[
        str,
        typer.Argument(metavar="ID", help="The draft to issue, or enough of its id to name it."),
    ],
) -> None:
    """Number a draft, render its PDF, and commit both."""
    repo, cfg = get_repo(ctx)
    invoice = repo.resolve(ident)
    _refuse_unless_draft(invoice)
    invoice.validate_issuable()
    if invoice.draft_id is None:
        # Only reachable from a hand-written file: `validate_issuable` has
        # already refused anything carrying a number, so what is left is a
        # draft with no id, which is not filed under a name to find it by.
        raise ValidationError(MISFILED_MESSAGE)
    # Asked before the counter is touched. Whether Pandoc is installed is
    # knowable up front, and a number that was never drawn needs no giving back.
    render.check_tools()

    # Kept as they stand, to be put back if any of what follows does not
    # finish: the draft's own two files, and the counter they are about to draw
    # from. Bytes rather than values, so the undo leaves no trace of itself —
    # a `sequence.toml` that says 1 where it used to say nothing at all is the
    # same counter, but it is not the same file, and git would show it.
    draft_toml = repo.invoice_path(invoice)
    draft = _Snapshot.of(draft_toml, draft_toml.with_suffix(PDF_SUFFIX))
    counter = _Snapshot.of(repo.sequence_path)

    today = clock.today()
    seq, number = sequence.allocate(repo, cfg, today.year)
    created: list[Path] = []

    try:
        invoice.number = number
        invoice.issue_date = today
        invoice.due_date = today + dt.timedelta(days=invoice.payment_terms_days)
        invoice.transition(InvoiceStatus.ISSUED, on=today)

        # `move_draft_to_issued` is the one write allowed to file a non-draft:
        # it writes the numbered file, clears the draft id, and removes the
        # draft. It refuses to overwrite an invoice that is already there, so
        # it has either created this path or touched nothing.
        issued_path = repo.move_draft_to_issued(invoice)
        created.append(issued_path)

        pdf_path = repo.pdf_path(invoice)
        # Recorded as ours before the render rather than after: the number it
        # is named for is about to be given back, so whatever ends up at that
        # path is this attempt's and nobody else's.
        created.append(pdf_path)
        render.render_pdf(repo, invoice, cfg, pdf_path)

        # The draft's paths go in so that its removal is part of this commit
        # too: the invoice moving out of drafts/ is one change to the repo, and
        # a history where the draft lingers for a commit is a history that lies.
        gitrepo.commit(
            repo.root,
            [issued_path, pdf_path, repo.sequence_path, *draft.paths],
            ISSUE_COMMIT_MESSAGE.format(number=number),
        )
    except BaseException as failure:
        _undo(repo, cfg, draft, counter, created, today.year, seq, failure)
        raise

    typer.echo(ISSUED_MESSAGE.format(number=number))
    typer.echo(TOTAL_MESSAGE.format(total=format_money(invoice.total, cfg.defaults.locale)))
    typer.echo(PDF_MESSAGE.format(path=pdf_path))


def render_invoice(
    ctx: typer.Context,
    ident: Annotated[
        str,
        typer.Argument(metavar="ID", help="The invoice or draft to render."),
    ],
    open_: Annotated[
        bool,
        typer.Option("--open", help="Open the PDF when it has been written."),
    ] = False,
) -> None:
    """Render an invoice's PDF again, and commit it only if it changed."""
    repo, cfg = get_repo(ctx)
    invoice = repo.resolve(ident)
    path = repo.pdf_path(invoice)
    name = invoice_id(invoice) or ident

    before = path.read_bytes() if path.is_file() else None
    # Rendered straight over the old PDF: Pandoc builds the document in a
    # temporary file of its own, so a render that fails leaves the file that is
    # already there untouched rather than truncated.
    render.render_pdf(repo, invoice, cfg, path)

    if path.read_bytes() == before:
        # Not a commit, not even an empty one. Re-rendering an invoice nobody
        # has edited is something a user may do out of doubt, and it should
        # leave no mark on the history at all.
        typer.echo(UP_TO_DATE_MESSAGE)
    else:
        gitrepo.commit(repo.root, [path], RENDER_COMMIT_MESSAGE.format(ident=name))
        typer.echo(RENDERED_MESSAGE.format(ident=name, path=path))

    if open_:
        typer.launch(str(path))


@dataclass(frozen=True)
class _Snapshot:
    """Some files as they stood, kept to put back if what follows does not finish.

    Bytes rather than the values they parse to. What has to come back after a
    failure is the file as it was found, down to the whitespace the user put in
    it, and not billkeeper's own rendering of what it read. A file that was not
    there is remembered as absent, so restoring it removes it again.
    """

    files: tuple[tuple[Path, bytes | None], ...]

    @classmethod
    def of(cls, *paths: Path) -> _Snapshot:
        """Return what is at each of `paths` right now, absence included."""
        return cls(tuple((path, path.read_bytes() if path.is_file() else None) for path in paths))

    @property
    def paths(self) -> tuple[Path, ...]:
        """Return the paths covered, whether or not anything is at them."""
        return tuple(path for path, _ in self.files)

    def restore(self) -> None:
        """Put every file back exactly as it was."""
        for path, content in self.files:
            if content is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write_bytes(path, content)


def _refuse_unless_draft(invoice: Invoice) -> None:
    """Raise unless `invoice` is still a draft.

    What state the invoice is in is named rather than described: "it is paid" is
    the whole answer to why it cannot be issued.
    """
    if invoice.status != InvoiceStatus.DRAFT:
        raise ImmutableInvoiceError(
            NOT_A_DRAFT_MESSAGE.format(
                ident=invoice_id(invoice) or "This invoice", status=invoice.status.value
            )
        )


def _undo(
    repo: Repo,
    cfg: RepoConfig,
    draft: _Snapshot,
    counter: _Snapshot,
    created: list[Path],
    year: int,
    seq: int,
    failure: BaseException,
) -> None:
    """Put the repo back as it was before invoice number `seq` was drawn.

    The order matters. The files this attempt wrote go first, then the draft
    comes back, and the number is given back last — through `sequence.rollback`
    rather than by writing the old counter straight back, because rollback is
    what refuses to hand a number back that something else has already been
    numbered on top of. Only once it has agreed is the file itself restored, so
    a counter that is logically unchanged is also unchanged on disk.

    An undo that cannot finish is the one thing here worth interrupting the
    original failure to say, so it is reported with both halves and the
    original failure kept as its cause.
    """
    try:
        for path in created:
            path.unlink(missing_ok=True)
        draft.restore()
        sequence.rollback(repo, cfg, year, seq)
        counter.restore()
    except (BillkeeperError, OSError) as trouble:
        raise BillkeeperError(
            UNDO_FAILED_MESSAGE.format(
                ident=draft.paths[0].stem,
                failure=_said(failure),
                trouble=_said(trouble),
                root=repo.root,
            )
        ) from failure


def _said(error: BaseException) -> str:
    """Return the sentence `error` carries, for one printed inside another."""
    if isinstance(error, BillkeeperError):
        return error.message
    return str(error) or error.__class__.__name__
