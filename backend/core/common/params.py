import pandas as pd

# Axial / length labels written into mold DXF TEXT (mm). Diameters stay as-is.
DXF_LENGTH_LABELS = frozenset({
    'L', 'L0', 'L1', 'L2', 'L3', 'L4', 'Ln', 'LT', 'LL', 'LL1',
    'L_s', 'L_m', 'TL', 'T_L', 'T_e', 'T_LR', 'IL',
})


def format_length_value(value):
    """Round a length in mm to the nearest integer string for mold / DXF fill."""
    if value in (None, '/'):
        return '/' if value == '/' else ''
    text = str(value).strip()
    if text in ('', '/'):
        return text
    try:
        return str(int(round(float(text))))
    except (TypeError, ValueError):
        return str(value)


def relief_hole_values(diameters):
    """减重孔：d1–d4 中大于 0 的最小值大于 50 时，直径 30、深度 400，否则都为 0。"""
    positive = []
    for value in diameters:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            positive.append(number)
    if positive and min(positive) > 50:
        return '30', '400'
    return '0', '0'


def format_dxf_text(label, value):
    """Format a DXF TEXT fill: length labels become integers, others unchanged."""
    if label in DXF_LENGTH_LABELS:
        return format_length_value(value)
    return str(value)


def df_to_dict(df):
    """
    Convert a parameter DataFrame to a dictionary.
    Supports both ('Parameter', 'Value') and ('参数', '值') column formats.
    """
    result = {}
    if df is None or df.empty:
        return result

    if 'Parameter' in df.columns and 'Value' in df.columns:
        for _, row in df.iterrows():
            result[row['Parameter']] = row['Value']
    elif '参数' in df.columns and '值' in df.columns:
        for _, row in df.iterrows():
            result[row['参数']] = row['值']
    return result


def dict_to_df(params_dict, columns=('参数', '值')):
    """Convert a dictionary to a parameter DataFrame."""
    return pd.DataFrame({
        columns[0]: list(params_dict.keys()),
        columns[1]: list(params_dict.values())
    })


def normalize_params_df(df):
    """
    Normalize a parameter DataFrame to always use ('参数', '值') columns.
    """
    if df is None or df.empty:
        return df
    if 'Parameter' in df.columns and 'Value' in df.columns:
        df = df.rename(columns={'Parameter': '参数', 'Value': '值'})
    return df
