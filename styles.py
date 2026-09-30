"""
Visual language for ARXIIV.STUDIO — black background, white monospace text,
thin hairline borders, bracketed section labels. Mirrors the reference UI.
"""

FONT_FAMILY = "'JetBrains Mono', 'Consolas', 'Courier New', monospace"

STYLE_SHEET = f"""
* {{
    font-family: {FONT_FAMILY};
    outline: none;
}}

QWidget {{
    background-color: #000000;
    color: #FFFFFF;
    font-size: 12px;
}}

QMainWindow {{
    background-color: #000000;
}}

QLabel#brand {{
    color: #FFFFFF;
    font-size: 13px;
    font-weight: bold;
    letter-spacing: 2px;
}}

QLabel#sectionTitle {{
    color: #F5F5F5;
    font-size: 11px;
    font-weight: bold;
    padding-top: 10px;
    padding-bottom: 2px;
}}

QLabel#dim {{
    color: #7A7A7A;
    font-size: 11px;
}}

QFrame#panel {{
    background-color: #050505;
    border: 1px solid #2A2A2A;
}}

QFrame#hairline {{
    background-color: #262626;
    max-height: 1px;
    min-height: 1px;
}}

QPushButton {{
    background-color: #0A0A0A;
    border: 1px solid #333333;
    color: #FFFFFF;
    padding: 9px 12px;
}}
QPushButton:hover {{
    background-color: #151515;
    border: 1px solid #6A6A6A;
}}
QPushButton:pressed {{
    background-color: #FFFFFF;
    color: #000000;
}}
QPushButton:checked {{
    background-color: #FFFFFF;
    color: #000000;
    border: 1px solid #FFFFFF;
}}
QPushButton:disabled {{
    color: #555555;
    border: 1px solid #222222;
}}

QPushButton#danger:checked {{
    background-color: #FF4433;
    color: #000000;
    border: 1px solid #FF4433;
}}

QComboBox, QLineEdit, QKeySequenceEdit {{
    background-color: #0A0A0A;
    border: 1px solid #333333;
    color: #FFFFFF;
    padding: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: #0A0A0A;
    color: #FFFFFF;
    border: 1px solid #333333;
    selection-background-color: #FFFFFF;
    selection-color: #000000;
}}

QFrame#playlistPanel {{
    background-color: #050505;
    border: 1px solid #262626;
}}

QScrollArea#trackQueue {{
    background-color: #050505;
    border: 1px solid #262626;
}}

QWidget#trackQueueContent {{
    background-color: #050505;
}}

QWidget#trackTile {{
    background: transparent;
    border: none;
    border-radius: 6px;
}}
QWidget#trackTile:hover {{
    background: transparent;
}}
QWidget#trackTile[selected="true"] {{
    background: transparent;
}}
QCheckBox#trackSelect {{
    spacing: 0px;
    margin: 0px;
    padding: 0px;
}}
QCheckBox#trackSelect::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid #555555;
    background: #080808;
    border-radius: 3px;
}}
QCheckBox#trackSelect::indicator:hover {{
    border: 1px solid #FFFFFF;
}}
QCheckBox#trackSelect::indicator:checked {{
    background: #FFFFFF;
    border: 1px solid #FFFFFF;
}}
QWidget#trackTile[selected="true"] QLabel#trackNumber,
QWidget#trackTile[selected="true"] QLabel#trackTitle,
QWidget#trackTile[selected="true"] QLabel#trackMeta,
QWidget#trackTile[selected="true"] QLabel#trackHint,
QWidget#trackTile[selected="true"] QLabel#trackCover {{
    color: #FFFFFF;
}}

QPushButton#removeSelected {{
    padding-left: 12px;
    padding-right: 12px;
}}
QLabel#selectionCount {{
    min-width: 92px;
    padding: 5px 9px;
    border: 1px solid #333333;
    border-radius: 4px;
    background: #0B0B0B;
    color: #AAAAAA;
    font-size: 10px;
    font-weight: bold;
}}
QPushButton:checked {{
    background: #FFFFFF;
    color: #000000;
    border-color: #FFFFFF;
}}
QWidget#trackTile[current="true"] {{
    background: transparent;
    border: none;
}}
QWidget#trackTile[current="true"] QLabel#trackNumber,
QWidget#trackTile[current="true"] QLabel#trackTitle,
QWidget#trackTile[current="true"] QLabel#trackMeta,
QWidget#trackTile[current="true"] QLabel#trackHint {{
    color: #FFFFFF;
}}
QWidget#trackTile[current="true"] QLabel#trackCover {{
    color: #777777;
    background-color: #111111;
}}

QLabel#trackCover {{
    background-color: #111111;
    color: #4A4A4A;
    border: 1px solid #262626;
    border-radius: 4px;
    font-size: 16px;
    font-weight: bold;
}}
QLabel#trackNumber {{
    color: #686868;
    font-size: 10px;
    font-weight: bold;
}}
QLabel#trackTitle {{
    background: transparent;
    color: #F2F2F2;
    font-size: 12px;
    font-weight: bold;
}}
QLabel#trackMeta {{
    background: transparent;
    color: #8A8A8A;
    font-size: 9px;
    letter-spacing: 1px;
}}
QLabel#trackNumber {{
    background: transparent;
    color: #686868;
    font-size: 10px;
    font-weight: bold;
}}
QLabel#trackHint {{
    background: transparent;
    color: #777777;
    font-size: 14px;
}}

QWidget#trackGhost {{
    background-color: #161616;
    border: 1px solid #FFFFFF;
    border-radius: 6px;
}}
QWidget#trackGhost QLabel#trackTitle,
QWidget#trackGhost QLabel#trackNumber,
QWidget#trackGhost QLabel#trackHint {{
    color: #FFFFFF;
}}
QFrame#dragPlaceholder {{
    background-color: #090909;
    border: 1px dashed #5A5A5A;
    border-radius: 4px;
}}

QListWidget {{
    background-color: #050505;
    border: 1px solid #333333;
    color: #FFFFFF;
    padding: 5px;
}}
QListWidget::item {{
    background-color: #0A0A0A;
    border: 1px solid #1E1E1E;
    padding: 9px 10px;
    margin: 1px 0px;
    border-radius: 2px;
}}
QListWidget::item:hover {{
    background-color: #151515;
    border: 1px solid #3A3A3A;
}}
QListWidget::item:selected {{
    background-color: #FFFFFF;
    color: #000000;
    border: 1px solid #FFFFFF;
}}
QLineEdit {{
    background-color: #0A0A0A;
    border: 1px solid #333333;
    color: #FFFFFF;
    padding: 8px 10px;
    selection-background-color: #FFFFFF;
    selection-color: #000000;
}}

QScrollArea {{
    border: none;
}}
QScrollBar:vertical {{
    background: #000000;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #303030;
    min-height: 20px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0px;
}}

QSlider::groove:horizontal {{
    height: 2px;
    background: #333333;
}}
QSlider::handle:horizontal {{
    background: #FFFFFF;
    width: 10px;
    margin: -5px 0;
}}

QCheckBox {{
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 12px;
    height: 12px;
    border: 1px solid #555555;
    background: #0A0A0A;
}}
QCheckBox::indicator:checked {{
    background: #FFFFFF;
}}
"""
