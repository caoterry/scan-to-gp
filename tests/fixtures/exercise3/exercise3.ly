\version "2.24.0"
\include "predefined-guitar-fretboards.ly"
% scan-to-gp public test fixture 3: a vocal line over a guitar staff + TAB, the guitar in two
% voices (melody stems up, a held bass stems down, low ledger-line notes), chord diagrams above
% the vocal, a dashed barline, a vocal-only system ("w/ Rhy. Fig. 1"), a bend label over the TAB,
% a capo legend and a shuffle tempo mark.
% Original music written for this project. License: CC0 / public domain.
% Guitar pitches are FINGERED pitches (book convention for capo pieces: notation in the fingered
% key, TAB frets counted from the capo); the staff is printed an octave higher, like exercise 1.

\header { title = "scan-to-gp test exercise 3" subtitle = "voice and guitar in two voices (original, CC0)" tagline = ##f }
\paper { #(set-paper-size "letter") indent = 0 ragged-last = ##f }
\markup \column { \line { "Capo on fret 2" } }

chrds = \chordmode { e1:m | g | c | e:m | a:m | g | c | e:m | e:m | g | c | e:m }

vox = \relative c'' {
  \key g \major \time 4/4 \tempo "Easy Shuffle" 4 = 100
  r1 | r1 | r2 r4 b8 c | b2. r4 | \break
  a4^\markup { \bold "w/ Rhy. Fig. 1" } a8 b c4 b | g2 r | e4 e8 g a4 g | e1 | \break
  b'2 a | g4 a b2 | c4 b a g | e1 \bar "|."
}
words = \lyricmode { oh my __ love, I sing a lit -- tle song, slow and low and long, a -- way, a -- way to -- day, gone. }

up = {
  \voiceOne
  b4\2 d'4\2 b4\2 a4\3 | g4\3 a8\3 b8\2 d'4\2 b4\2 \bar "!" |
  e'4\1 d'4\2 b4\2 g4\3 | g2\3 r2 | \break
  s1*4 | \break
  e'4\1 d'4\2 b4\2 a4\3 | g4\3 a8\3 b8\2 d'4\2 b4\2 | e'4\1 d'4\2 b4\2 g4\3 | g2\3 r2 \bar "|."
}
down = {
  \voiceTwo
  e,2\6 a,2\5 | e,1\6 | e,2\6 b,2\5 | e,1\6 |
  s1*4 |
  e,2\6 a,2\5 | e,1\6 | e,2\6 b,2\5 | e,1\6
}
upTab = { b4\2 d'4\2 b4\2 a4\3 | g4\3 a8\3 b8\2 d'4\2 b4\2 \bar "!" |
  e'4\1 d'4\2 b4\2^"1/2" g4\3 | g2\3 r2 | s1*4 |
  e'4\1 d'4\2 b4\2 a4\3 | g4\3 a8\3 b8\2 d'4\2 b4\2 | e'4\1 d'4\2 b4\2 g4\3 | g2\3 r2 }

\score {
  <<
    \new FretBoards { \chrds }
    \new Staff = "vocal" { \clef treble \new Voice = "v" \vox }
    \new Lyrics \lyricsto "v" \words
    \new Staff \with { \omit StringNumber \RemoveAllEmptyStaves } {
      \clef treble \key g \major \time 4/4
      << \new Voice \transpose c c' \up \new Voice \transpose c c' \down >>
    }
    \new TabStaff \with { stringTunings = #guitar-tuning \RemoveAllEmptyStaves \revert TextScript.stencil } {
      << \new TabVoice \upTab \new TabVoice \down >>
    }
  >>
  \layout { }
}
\score {
  << \new Staff { \key g \major \time 4/4 << \up \\ \down >> } >>
  \midi { }
}
