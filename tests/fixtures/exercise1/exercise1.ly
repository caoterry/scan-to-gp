\version "2.24.0"
% scan-to-gp public test fixture. Original exercise written for this project
% (B minor pentatonic + blues note). License: CC0 / public domain.
% Pitches below are SOUNDING pitches; the staff is printed an octave higher
% (standard guitar notation in plain treble clef), the TAB uses standard tuning.

\header { title = "scan-to-gp test exercise 1" subtitle = "B minor pentatonic workout (original, CC0)" tagline = ##f }
\paper { #(set-paper-size "letter") indent = 0 ragged-last = ##f }

music = {
  \key d \major \time 4/4
  \partial 4 b,8\6 d8\6 |
  \tuplet 3/2 { e8\5 fis\5 e\5 } \tuplet 3/2 { d\6 e\5 fis\5 } \tuplet 3/2 { a\4 fis\5 e\5 } d4\6 |
  b4.\4 a8\4 r4 <b,\6 fis\5>4 |
  e8\5 f!\5 f\5 fis!\5 a\4 b\4 a\4 fis\5 | \break
  d'4\3 e'\3 fis'2\2 ~ |
  fis'4\2 e'8\3 d'\3 b4\4 r4 |
  a16\4 b\4 a\4 fis\5 e8.\5 d16\6 b,2\6 \bar "|."
}

\score {
  <<
    \new Staff \with { \omit StringNumber } { \clef treble \transpose c c' \music }
    \new TabStaff \with { stringTunings = #guitar-tuning } { \music }
  >>
  \layout { }
}
\score {
  \unfoldRepeats \music
  \midi { \tempo 4 = 90 }
}
