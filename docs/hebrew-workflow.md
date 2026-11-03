# Distribution stability in 1.3

See [distribution.md](distribution.md) for installation identity, maintenance state, corruption recovery and persistent optional-model consent. MMS weights and cached MMS measurements now require explicit opt-in; the presence of a cached model no longer enables it. Existing saved user timings are preserved.

# Hebrew transcript extension

## Architecture

The upstream rendering pipeline is retained: word grouping → ASS/SRT builders →
FFmpeg burn-in. The original desktop GUI still invokes the CLI. The new default
web interface uses `web.py` (FastAPI on loopback) and buildless HTML/CSS/JavaScript
in `web_static/`, calling the same alignment, validation and rendering modules.
No frontend framework, remote media processing or hosted service is required.

The web UI uses a three-step Hebrew journey for Israeli users: video selection,
source text, then playback and download. Styles and timing tools are disclosed on
request; unresolved words automatically open a guided correction panel. Playback
position capture avoids requiring users to enter seconds. The source text draft
survives refresh in browser-local storage, and returning from text editing can
restore the existing report without rerunning alignment. Numeric precision and
waveform tools remain available, while raw model errors are kept out of user copy.

Each uploaded MP4 is copied to a UUID project under `.autocaption-web/`. Atomic
JSON records store transcript, settings, report and job state; a single background
worker performs alignment, independent speech checks and export. A restart marks interrupted jobs for retry.
Timing endpoints accept indexes, times and an explicit listening confirmation, never replacement words. Source
hashes and all word intervals are checked again before rendering. Video endpoints
support byte ranges for seeking. The browser renders a 600-bin audio waveform and
word timeline; preview-frame requests use the real libass renderer. Styles and
timing edits invalidate older exports. The loopback server rejects foreign hosts
and origins and serves its fonts, scripts and styles locally.

`alignment.py` adds a separate entry path. A frozen `Transcript` owns exact text
and derives indexed whitespace tokens without normalization. WhisperX loads only
the Hebrew alignment model and receives those tokens joined by spaces. No ASR
model is loaded by the alignment adapter. It verifies the complete returned word and character
sequences. Supported characters with real acoustic evidence contribute timings.
Foreign letters/numerals, failed alignments, low confidence, missing characters
and invalid intervals remain explicit unresolved rows.

A local UTF-8 JSON sidecar stores every word, the original transcript, media/text
hashes, scores, states and reasons. `report_words` is the export gate. The GUI
always presents a timing review after alignment; the CLI can export directly
when every word validates. `--align-only` saves the report; `--timings` resumes
without inference and rechecks the exact source.

The web workflow additionally invokes `verification.py`: the selected Hebrew or
multilingual model listens without receiving the source as a prompt. Its
tokens remain separate local evidence and never become displayed words. Exact
comparison keys ignore punctuation/case/niqqud only for checking; no fuzzy lexical
replacement is allowed. The matcher consumes each recognized occurrence at most
once, in order and within a bounded time neighborhood. Confidence, interval
overlap and both boundary differences determine estimated agreement (80 ms
tolerance on each edge, confidence >= .70, acoustic score >= .35, temporal IoU
>= .65). Recognized evidence is acoustically aligned in Hebrew as well, with
coarse ASR boundaries retained for audit and a 750 ms maximum refinement. Both
timelines use the same acoustic model; this is not independent proof of accuracy.
The web studio now completes timing automatically using `automatic_timing.py`.
Exact monotonic token matches can supply missing/weak acoustic anchors; raw ASR
boundaries are a labelled fallback. Remaining gaps are allocated proportionally
between anchors, preserving valid manual edits and every source token. Estimated
rows retain original times/status/reasons for optional inspection; they are never
marked as listened. Diagnostics do not block preview or export. Invalid manual
intervals and transcript/media fingerprint mismatches still fail validation.
Legacy partial reports are migrated with a backup; existing exports are invalidated
when timings change. The editor opens on a visible subtitle, with design selected
and model settings collapsed. A bad legacy interval cannot blank all other lines.
Evidence caches are bound to media hash, concrete model and evidence version.

`hardware.py` selects a concrete model before each automatic job. On CPU, Hebrew
Large requires >=12 GiB available RAM and >=6 physical cores; Hebrew Turbo needs
>=6 GiB and >=4 cores; Small needs >=2.5 GiB, otherwise Base is selected. Usable
CUDA with >=7 GiB free VRAM and >=6 GiB RAM selects Large; >=4 GiB VRAM and >=4 GiB
RAM selects Turbo. These thresholds reserve working room and are heuristics,
not universal minimum requirements or an accuracy benchmark. GPU detection checks
inference libraries and free VRAM, not just an adapter name. Memory failures in
automatic mode can step down explicitly; network failures and manual selections
do not. New web projects default to automatic selection. Auto-generated transcripts
also get Hebrew forced alignment, preserving all their words if alignment fails.

Transcript mode disables capitalization and gap snapping. ASS retains logical
Unicode order with libass's paragraph direction detection (`Encoding=-1`) and a
right-to-left mark for mixed Hebrew lines. Noto Sans Hebrew Bold covers Hebrew,
niqqud, Latin, digits and ordinary punctuation. Pop and zoom use eased scale
curves that settle at 100% before the word ends. Float uses a subtle entrance;
karaoke retains its color sweep and real gaps with timed invisible separators.
Neutral events bridge within-line pauses without extending acoustic word times.
The browser obtains the shared motion contract from `/api/motion` and applies the
same strength/speed settings as ASS. Drag/resize, rotation, colors and positioning
apply to all lines. Peak rotated bounds keep text inside the original frame.
Rendering revisions invalidate old exports without deleting their files.

FFmpeg decodes a mono 16 kHz copy for alignment, preserving initial audio delay.
Export uses the original video and copied audio. Temporary ASCII-only ASS/font
paths avoid filter-escaping bugs. Output is staged beside the destination until
encoding succeeds.

## Practical limits

- First setup needs internet to download public packages, model and tokenizer
  resources. Torch and WhisperX dependencies also require disk space. Media and
  transcripts remain local.
- This workflow targets short Reels. A single alignment window is used; long
  recordings use more memory and are slower. There is no ASR-based transcript
  rewriting or segmentation fallback.
- The strict alignment adapter leaves unsupported words/numerals unresolved.
  The web completion layer supplies labelled estimates; optional manual editing
  remains available. No alignment or recognition output replaces source words.
- Independent recognition is fallible and cannot prove that a word was spoken.
  Recognition disagreement, silence, accents and spelling differences can reduce
  timing accuracy. Manual refinement is optional, never an approval requirement.
- The web editor contains an embedded player, waveform, seekable word timeline,
  numeric timing fields, playback-position capture and word audition. Browser
  playback depends on browser codec support (H.264/AAC MP4 is recommended).
- The browser's live text overlay approximates the ASS animation. Use the
  rendered-frame button to verify exact libass shaping and animation appearance.
  The original desktop preview uses starting tokens on a neutral background.
  Long lines/tokens are sized to fit the frame, allowing for peak active-word
  zoom. Bundled font metrics are measured; unknown custom fonts use conservative
  bounds and should be checked in preview.
- ASS bidi extensions target FFmpeg/libass. Other subtitle players may lay out
  text differently. Burned MP4s preserve the verified layout.
- Burning subtitles necessarily re-encodes video pixels. Audio packets are
  copied. Export fails for audio codecs the MP4 container cannot carry.

## Sources

- [WhisperX alignment v3.8.5](https://github.com/m-bain/whisperX/blob/v3.8.5/whisperx/alignment.py)
- [Hebrew alignment model](https://huggingface.co/imvladikon/wav2vec2-xls-r-300m-hebrew)
- [Noto Sans Hebrew](https://github.com/google/fonts/tree/main/ofl/notosanshebrew)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [ASS animation tags](https://aegisub.org/docs/latest/ass_tags/)

The bundled fonts are static instances of the upstream variable font at weights
400 and 700 and width 100, generated with fontTools and updated family/style names.
The original copyright and OFL text accompany it.


## Whole verses and choruses

The web studio has `caption_mode=words|segments` and `word_animation` settings.
Word mode groups exactly the selected number (1–6), except the final remainder;
the old 18-character/sentence/gap thresholds no longer override a web selection.
Desktop/CLI grouping keeps its existing defaults. Font fitting keeps long lines
within the source frame. Motion revision 5 invalidates exports using old grouping.

`segments.py` stores ordered text blocks with a label, start and end. Blank lines
split passages; single line breaks remain inside the displayed verse. Preparing
passages can start directly from pasted text without downloading or running ASR
or alignment. Existing word timings seed initial passage bounds when available.
These are editable starting points, not approved manual timings. Existing exact
transcript tokens, including punctuation/repetitions, must be covered once in the
original order; omission, replacement, reordering, overlaps and out-of-range times
are rejected. Paragraph reflow does not replace the original transcript string.

The two timelines are independent: switching back to words restores the original
word report; switching back to passages restores saved passage timing. The editor
supports labels, whole-text paragraph splitting, start/end capture from the player,
`MM:SS.cc` time fields and immediate preview. Valid changes autosave; unfinished drafts
stay locally and cannot accidentally be exported instead of saved times.

Word and passage start/end fields display minutes, seconds and hundredths
(`03:26.16`), with one-click copy beside each field. Clicking the player clock
copies the current position. Untouched fields preserve the original sub-hundredth
alignment precision; typed rounded boundaries resolve to the exact neighbouring
bound when needed. Malformed times stay invalid rather than becoming zero.
Space toggles playback from time fields, buttons and timeline controls, while
transcript/label text entry and modal dialogs keep their normal keyboard behaviour.

The passage timeline sits below the live video. Drag a block to move its entire
interval, or either edge to trim the start/end independently. Time flows left to
right; Hebrew labels stay RTL. Zoom or focus a passage for smaller adjustments.
Arrow keys move the focused block/edge by 0.1s (Alt: 0.01s, Shift: 1s). Gestures
stop at adjacent passages and video bounds; the immutable text order is retained.
The preview and numeric fields update during the drag; the server saves on
release. Escape/pointer cancellation restores the original range, and Undo
restores the previous gesture. Numeric edits invalidate gesture undo history.

`פיצול כאן` targets the passage at the playhead, regardless of the selected
passage. The split dialog previews both text parts and lets the user choose the
first word of the second part. Existing matching word timings suggest a word
boundary when they still fit the passage; otherwise the proportional suggestion
is explicitly marked as approximate. The chosen video time is preserved to ASS
centisecond precision. Splitting uses original text substrings, retaining the
boundary whitespace, so merging the halves restores the exact original text.
Single-word passages and cuts at/outside a passage edge cannot be split.

`חיבור לקודם` / `חיבור לבא` merge the selected passage with its chronological
neighbour. The result spans both outer bounds, including any gap. Text stays in
source order; independent paragraphs are joined by a line break. Structural edits
share Undo with timing gestures, persist through the validated `/segments` API,
invalidate old exports, and update the live video, cards and timeline together.

The ASS renderer emits one static text event per passage, preserving RTL and explicit
line breaks with a shared multiline fit calculation. No active-word zoom/color or
karaoke is applied to passages. The same project styles, movement, rotation and
backdrop remain available. SRT exports one block per passage. Disabling word animation
in word mode also removes individual highlights/pulses without changing its timings.

## Word timeline and explicit manual ordering

`word-editing.js` handles bounded group moves, trims, chronological range/tail
selection and exact two-word time-slot swaps. `word-timeline.js` provides pointer
capture, live preview, edge scrolling, selection modes, keyboard nudges, Escape
cancellation and Undo/Redo. The word timeline is visible below the video in word
mode, including the mobile layout. The default collision mode borrows time from
neighbouring words. Dragging a body shifts its interval; dragging an edge resizes
it. When the interval crosses a neighbour, that neighbour's touched edge follows
the new boundary while its other edge stays fixed. This also applies to groups,
whose internal spacing remains unchanged; outer durations connect to the next word. Bounds reserve one
rendered hundredth for each neighbour, so no word can be consumed or reordered.
The whole gesture uses its initial snapshot, so dragging back, Escape and Undo
restore both affected boundaries. Changed neighbours highlight during the gesture
and save in the same PATCH as the selected words.

The optional **מחליפים את הזמנים** collision mode retains single-word swaps of both
start/end pairs and clamps groups at unselected neighbours. No text or source index
is mutated in either mode.

The existing `/timings` PATCH saves all changed words in one transaction. Only an
explicit swap opts into `allow_reorder`; the report then records
`allow_reordered_timings`. Source rows and transcript remain in their original
order, while `display_rows()` supplies chronological order to validation, automatic
completion, preview grouping, ASS and SRT export. Reports without the flag retain
the strict source-order validation. Overlaps remain invalid in both modes. Passage
mode keeps its original text order and does not seed its boundaries from swapped
word slots. A failed save restores the browser's last saved timings.

`fillGaps()` extends only end times to the next chronological start. Initial load,
completed alignment and a switch to word mode apply this as one undoable batch.
Pointer/keyboard/manual timing edits also fill their resulting gaps. Shortening
an end handle moves the next start to the same boundary; it must not be negated
by re-extending that end. No extension precedes the first word, follows the last,
or bridges an invalid/unaligned row. Passage timing is independent of gap filling.

`shortcuts.js` resolves physical key codes (including Hebrew layouts) and marks
history entries with nonserialized order metadata. Global undo/redo chooses the
most recent entry from style and the active word/passage editor. A new edit clears
redo branches across editors. Inputs, composition and noncanvas dialogs keep native
keyboard handling. Timeline arrows and caption-position arrows consume their local
actions before the playback shortcuts; the player also exposes explicit seek buttons
and an RTL keyboard reference. History lasts for the current page session.

## Acoustic refinement and display holds

Recognition evidence version 3 disables speech VAD for the evidence pass, so sung
phrases are not excluded before lexical matching. `alignment_windows.py` uses
monotonic contextual matches only as search bounds. Source line breaks, ten-word
limits and a thirty-second acoustic window limit prevent a whole recording from
collapsing into an earlier chorus. Each WhisperX call validates its own complete
source sequence. Unsupported windows retain every source row and get explicit
estimates from `complete_timings`; they are not reported as verified speech.

The improve action now reruns alignment, including already aligned automatic rows.
Manual rows stay fixed by default. A pre-refinement report is backed up before any changes.
An exact pre-gap-fill backup can recover provenance for projects from the old UI;
unprovable edits are left manual. `gap_fill`/`gap_fill_undo` PATCHes validate that
only the display end was extended/restored, retain acoustic/manual status and keep
the pre-extension end. Re-alignment uses that end for manual anchors. Its completion
clears stale word undo history, then reapplies display holds as a new undoable batch.

## Automatic retries for singing

`singing_timing.py` retries remaining estimates after the ordinary alignment pass.
It isolates vocals in temporary clips with TorchAudio's HDEMUCS_HIGH_MUSDB_PLUS
(about 319 MB downloaded once), then runs the same Hebrew WhisperX aligner on the
exact source line. A bounded second pass adds neighbouring source context. Both
boundaries must agree across at least two differently cropped alignments; weak,
clipped or isolated proposals cannot become acoustic anchors. A monotonic path
chooses compatible candidates together, rather than combining conflicting
per-word winners. Overlaps up to 80 ms can share a boundary, with the acoustic end
recorded separately. This is model agreement, not a claim of human-level accuracy.

The retry changes estimated words and only the neighbouring slots that conflict
with them. The **לתקן גם תזמון ידני שמתנגש בשירה — עם גיבוי** checkbox opts in to
releasing conflicting manual anchors; the preference persists per project. Other
manual edits stay fixed. Reports preserve `before_acoustic_retry`, any replaced
manual interval, crop support and the pre-refinement file. Successful vocal
anchors survive later general rechecks. Download/runtime failures retain explicit
estimates and do not prevent preview/export. Vocal audio is a local analysis cache;
the source video, exported audio, source tokens and their order remain unchanged.

## Whole-recording timing audit

`timing_audit.py` evaluates every source token independently of its saved
`aligned` label. Fresh recognition of separated vocals and recognition of the
original audio are matched monotonically against immutable source words. Two
audio views of one recognizer do not count as two models. Stable forced-alignment
proposals must also agree with two recognizer models before supplying support.
The report distinguishes `supported`, `disagreement`, `uncertain` and `stale`.
Support is automated evidence, not a guarantee of exact timing or human listening.
Missing recognition never removes a source word. Changes to real word boundaries
invalidate the audit; display-only gap fills retain their original acoustic end.

The full audit is a separate analysis, not the ordinary automatic-refinement
button. Its evidence and per-word results are saved in `full_audit` and `audit`.
Exact-token recognition consensus can supply explicitly estimated timings when
singing defeats forced alignment; that fallback does not manufacture acoustic
verification. Preserve a full project/timing backup before applying corrections.
Karaoke previews show the whole caption line in advance and animate its active
word. A line's first visibility, word highlight onset, acoustic end and extended
display hold are different times and must not be presented as interchangeable.

## Automatic recovery of uncertain singing

`singing_alignment.py` is now invoked by automatic refinement for an audited
project, and for longer recordings with many uncertain words. It remeasures exact
source tokens in overlapping phrase windows, using two Hebrew wav2vec checkpoints
loaded through WhisperX and forced attention paths from multilingual Whisper.
The second Hebrew checkpoint is `imvladikon/wav2vec2-large-xlsr-53-hebrew`.
Waveforms are normalized according to the checkpoints' feature-extractor contract.
CTC uses repeated-label paths for held notes; punctuation and niqqud are removed
only from its analysis representation. All displayed source tokens remain exact.

Separated vocals and a pitch-preserving 1.35x analysis view help with sustained
singing. Analysis timestamps are converted back to source-video time. The original
audio remains the export source. A global source-ordered path resolves conflicting
automatic anchors together. Manual locks require the existing persistent opt-in.
Missing or weak evidence retains an explicit estimate; it never drops a word or
counts repeated crops of one checkpoint as independent models. Single-letter
abbreviations cannot be verified from a single short CTC phoneme.

The report records onset and end support separately. Automatic support requires
two different models, including CTC evidence; it is not a promise of exact timing.
Each checkpoint's repeated measurements must also be consistent: an isolated
tempo/crop outlier cannot verify a boundary across an instrumental break. Candidate
selection uses median per-checkpoint distances instead of cherry-picking a crop.
Unsupported results remain playable/exportable without per-word approvals.
Measurement caches are bound to the source transcript, media fingerprint, model
choice and recovery version. The ordinary improvement button reuses them, keeping
the workflow automatic. Project snapshots are saved before applying refinement.

Model/input references: [Hebrew XLS-R](https://huggingface.co/imvladikon/wav2vec2-xls-r-300m-hebrew),
[Hebrew XLSR-53](https://huggingface.co/imvladikon/wav2vec2-large-xlsr-53-hebrew),
[CTC alignment API](https://docs.pytorch.org/audio/stable/generated/torchaudio.functional.forced_align.html).

### Additional automatic checks for uncertain singing

`singing_precision.py` retries uncertain words in short overlapping phrases, using
the existing Hebrew acoustic models. Context words stabilize each crop but do not
count as extra measurements. Conventional readings of `ה׳` are analysis-only;
the exact source word and its index always remain unchanged on screen and in exports.
Starts and ends can come from different measured paths only when both boundaries
already have independent support and belong to the same nearby occurrence.
No timestamp is created by interpolating between two words.

The optional `precision` extra supplies uroman. With explicit consent saved in
computer preparation and MMS_FA weights in the local Torch cache, the additional
multilingual model runs locally. Cached weights alone do not enable it.
These weights have a separate **CC-BY-NC 4.0** license, unlike the app's MIT code;
they are not bundled or automatically downloaded by the application. See the
[model documentation](https://docs.pytorch.org/audio/stable/generated/torchaudio.pipelines.MMS_FA.html).
The two Hebrew aligners continue to work without this optional model.

When available, the additional model also compares each selected interval with a
copy in which only that interval is muted. The report records both probabilities,
the measured paths, and the exact tested times. This is diagnostic evidence of
dependence on the audio, not a guarantee of accurate boundaries. A check explicitly
records whether it still matches the final timing; later edits cannot inherit it.
The original audio is never muted or replaced. Precision caches are fingerprinted
by both the media and immutable transcript and are reused by the ordinary automatic
improvement button. Conflicting or weak measurements remain uncertain.

### Context and pronunciation retries

`singing_phrases.py` makes a further automatic pass over unresolved words. It
uses surrounding acoustic measurements to choose phrases, trims context across
instrumental breaks, and retains at least the target word. Saved/manual times
never define these crops. Both original audio and isolated vocals are measured.
The Hebrew CTC checkpoints also try the original full spelling, retaining vowel
letters such as ו in הכול that the canonical spelling analysis removes.

When the optional Phonikud ONNX model and MMS_FA are installed and explicitly enabled, Hebrew pronunciation
is generated locally and supplied to the multilingual acoustic aligner. Added
diacritics and phonemes are analysis-only. Every original letter, source index,
word boundary and punctuation mark is checked before that representation is used;
a changed letter or word count rejects the analysis. Known ה׳ expansions remain
analysis-only. Phonikud is a pronunciation aid, not another acoustic-model vote.
The original Hebrew and multilingual Whisper paths remain independent checks;
multiple crops or spellings of one checkpoint still count as one checkpoint.

The joint timing search now scores any shortening of a preceding word when a
following onset overlaps it. It no longer selects a path using one duration and
quietly substitutes a shorter duration afterward. The report retains measured
boundaries, model identities, unchanged support thresholds, and separate acoustic
ends versus display holds. No threshold is lowered to force the uncertain count
to zero. Cached phrase measurements are bound to the media and transcript; a later
retiming invalidates any audio-muting diagnostic for the old interval.

New CTC measurements also store first- and last-phoneme probabilities separately.
A high average score from interior consonants cannot certify a weak onset or end.
Once these direct edge scores exist for a checkpoint, its old whole-word averages
are no longer used as substitutes for edge confidence. This can legitimately reduce
the supported count when more specific evidence contradicts an old assessment.

The optional `precision` extra includes Phonikud's runtime. Its model weights are
only used when already cached locally. See the [MIT-licensed pronunciation model](https://huggingface.co/Phonikud/phonikud-onnx)
and [phonemizer documentation](https://github.com/phonikud/phonikud). The separately
licensed optional MMS_FA conditions above continue to apply.
