\version "2.24.0"
% scan-to-gp public test fixture 2: two guitar parts stacked under one system line
% (Gtr. 1 melody over Gtr. 2 accompaniment), a half-step-down tuning legend, a tempo mark,
% a section label + tempo change between the systems, an indented first system.
% Original music written for this project. License: CC0 / public domain.
% Pitches below are FINGERED pitches (the book convention for tuned-down guitar: notation and
% TAB as if in standard tuning); the staff is printed an octave higher, like exercise 1.

\header { title = "scan-to-gp test exercise 2" subtitle = "two guitars in one system (original, CC0)" tagline = ##f }
\paper { #(set-paper-size "letter") ragged-last = ##f }
\markup \column { \line { "All strings down 1/2 step:" } \line { "(6)=Eb  (5)=Ab  (4)=Db  (3)=Gb  (2)=Bb  (1)=Eb" } }

gOne = {
  \key g \major \time 3/4 \tempo "Moderately" 4 = 120
  d'2.\2 | b4\2 c'4\2 d'4\2 | e'2\1 d'4\2 | b2.\2 | \break
  \sectionLabel "Chorus" \tempo "Faster" 4 = 140
  c'4\2 e'4\1 g'4\1 | fis'2\1 e'4\1 | d'4\2 c'4\2 a4\3 | g2.\3 \bar "|."
}
gTwo = {
  \key g \major \time 3/4
  g,4\6 <d\4 g\3> <d\4 g\3> | b,4\5 <d\4 g\3> <d\4 g\3> | c4\5 <e\4 g\3> <e\4 g\3> | g,4\6 <d\4 g\3> <d\4 g\3> |
  a,4\5 <e\4 a\3> <e\4 a\3> | d4\4 <a\3 c'\2> <a\3 c'\2> | d4\4 <a\3 c'\2> <a\3 c'\2> | g,4\6 <d\4 g\3> <d\4 g\3> \bar "|."
}

\score {
  \new StaffGroup \with { systemStartDelimiter = #'SystemStartBar \remove "Span_bar_engraver" } <<
    \new Staff \with { instrumentName = "Gtr. 1" \omit StringNumber } { \clef treble \transpose c c' \gOne }
    \new TabStaff \with { stringTunings = #guitar-tuning } { \gOne }
    \new Staff \with { instrumentName = "Gtr. 2" \omit StringNumber } { \clef treble \transpose c c' \gTwo }
    \new TabStaff \with { stringTunings = #guitar-tuning } { \gTwo }
  >>
  \layout { }
}
\score {
  << \new Staff \gOne \new Staff \gTwo >>
  \midi { }
}
