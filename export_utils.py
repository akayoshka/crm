import io
import csv
from datetime import datetime
from flask import send_file


def export_to_csv(data, headers, filename):
    """
    Export data to CSV file

    Args:
        data: List of lists containing row data
        headers: List of column headers
        filename: Name of the file (without extension)

    Returns:
        Flask response with file download
    """
    output = io.StringIO()
    writer = csv.writer(output)

    # Write headers
    writer.writerow(headers)

    # Write data rows
    for row in data:
        writer.writerow(row)

    # Prepare response
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f"{filename}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    )


def export_to_excel(data, headers, sheet_name, filename):
    """
    Export data to Excel file

    Args:
        data: List of lists containing row data
        headers: List of column headers
        sheet_name: Name of the worksheet
        filename: Name of the file (without extension)

    Returns:
        Flask response with file download
    """
    try:
        import xlsxwriter
    except ImportError:
        # If xlsxwriter is not installed, fall back to CSV
        print("Warning: xlsxwriter not installed. Falling back to CSV export.")
        return export_to_csv(data, headers, filename)

    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output)
    worksheet = workbook.add_worksheet(sheet_name)

    # Add header formatting
    header_format = workbook.add_format({
        'bold': True,
        'bg_color': '#f2f2f2',
        'border': 1
    })

    # Add data cell formatting
    cell_format = workbook.add_format({
        'border': 1
    })

    # Write headers
    for col, header in enumerate(headers):
        worksheet.write(0, col, header, header_format)

    # Write data rows
    for row_idx, row in enumerate(data):
        for col_idx, cell_value in enumerate(row):
            worksheet.write(row_idx + 1, col_idx, cell_value, cell_format)

    # Auto-adjust column widths
    for i, header in enumerate(headers):
        worksheet.set_column(i, i, max(len(header) + 2, 12))

    workbook.close()

    # Prepare response
    output.seek(0)
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f"{filename}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    )


def export_to_pdf(data, headers, title, filename):
    """
    Export data to PDF file

    Args:
        data: List of lists containing row data
        headers: List of column headers
        title: Title of the document
        filename: Name of the file (without extension)

    Returns:
        Flask response with file download
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter, landscape
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
    except ImportError:
        # If reportlab is not installed, fall back to CSV
        print("Warning: reportlab not installed. Falling back to CSV export.")
        return export_to_csv(data, headers, filename)

    output = io.BytesIO()

    # Create the PDF document
    doc = SimpleDocTemplate(
        output,
        pagesize=landscape(letter),
        title=title
    )

    # Get styles
    styles = getSampleStyleSheet()
    title_style = styles['Heading1']

    # Create document elements
    elements = []

    # Add title
    elements.append(Paragraph(title, title_style))
    elements.append(Spacer(1, 20))

    # Create the table with headers and data
    table_data = [headers] + data
    table = Table(table_data)

    # Add table styling
    style = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ])

    # Add alternating row colors
    for i in range(1, len(table_data)):
        if i % 2 == 0:
            style.add('BACKGROUND', (0, i), (-1, i), colors.whitesmoke)

    table.setStyle(style)
    elements.append(table)

    # Build the PDF
    doc.build(elements)

    # Prepare response
    output.seek(0)
    return send_file(
        output,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f"{filename}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    )


# Функция-помощник для преобразования данных объектов в список списков
def prepare_data_for_export(objects, attributes, formatters=None):
    """
    Prepare data from database objects for export

    Args:
        objects: List of database model objects
        attributes: List of attribute names to include
        formatters: Dictionary mapping attribute names to formatter functions

    Returns:
        List of lists containing row data
    """
    if formatters is None:
        formatters = {}

    data = []
    for obj in objects:
        row = []
        for attr in attributes:
            value = getattr(obj, attr)
            if attr in formatters:
                value = formatters[attr](value)
            row.append(value)
        data.append(row)

    return data