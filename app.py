from flask import Flask, render_template, request, jsonify, send_file
import re
import io
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'  # Change this in production
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# MARC processing functions
def get_p_values(text):
    """Extract all p values from the entire text"""
    # Updated regex to match your format: $p700 (without space after $)
    return sorted([int(match.group(1)) for match in re.finditer(r'=852\s+[^$]*\$p(\d+)', text)])

def get_852_field_template(records):
    """Get a template 852 field from any record that has one"""
    for record in records:
        match = re.search(r'(=852\s+[^\n]*)', record)
        if match:
            return match.group(1)
    return None

def find_missing_852_fields(records):
    """Find records missing 852 fields and determine what p values they need"""
    missing_fields = {}
    all_p_values = []
    
    # First, collect all existing p values to understand the sequence
    for record in records:
        p_values = get_p_values(record)
        all_p_values.extend(p_values)
    
    if not all_p_values:
        return missing_fields
    
    all_p_values = sorted(set(all_p_values))
    
    # Check each record for missing 852 fields
    for i, record in enumerate(records):
        current_p_values = get_p_values(record)
        
        if not current_p_values:  # Record has no 852 field
            # Determine what p value this record should have
            if i == 0:
                # First record should have the first p value
                if all_p_values:
                    missing_fields[i] = [all_p_values[0]]
            else:
                # Find the expected p value based on position
                prev_records_p_values = []
                for j in range(i):
                    prev_records_p_values.extend(get_p_values(records[j]))
                
                if prev_records_p_values:
                    expected_p = max(prev_records_p_values) + 1
                    missing_fields[i] = [expected_p]
                elif all_p_values:
                    missing_fields[i] = [all_p_values[0]]
    
    # Also check for gaps between consecutive records
    for i in range(len(records)-1):
        current_p_values = get_p_values(records[i])
        next_p_values = get_p_values(records[i+1])
        
        if current_p_values and next_p_values:
            last_p = max(current_p_values)
            first_next_p = min(next_p_values)
            
            missing = list(range(last_p + 1, first_next_p))
            if missing:
                if i in missing_fields:
                    missing_fields[i].extend(missing)
                else:
                    missing_fields[i] = missing
    
    return missing_fields

def add_missing_852_fields(record, missing_values, template_852):
    """Add new 852 fields for missing values to the record"""
    if not missing_values or not template_852:
        return record
    
    lines = record.splitlines()
    
    # Find where to insert the 852 field (after =653 fields or before =LDR if next record)
    insert_index = len(lines)  # Default to end
    
    # Look for a good insertion point (after subject fields, before next record)
    for i, line in enumerate(lines):
        if line.startswith('=653'):
            insert_index = i + 1
        elif line.startswith('=LDR') and i > 0:  # Next record starts
            insert_index = i
            break
    
    # Insert new 852 fields
    new_lines = lines[:insert_index]
    
    for missing_p in missing_values:
        # Create new 852 field with the missing p value
        new_852 = re.sub(r'\$p\d+', f'$p{missing_p}', template_852)
        new_lines.append(new_852)
    
    new_lines.extend(lines[insert_index:])
    
    return '\n'.join(new_lines)

def process_marc_records(input_text):
    """Process MARC records to ensure continuous p values"""
    # Split records more carefully
    records = []
    parts = input_text.strip().split('=LDR')
    
    if parts[0].strip():  # First part before any =LDR
        records.append(parts[0].strip())
    
    for part in parts[1:]:
        if part.strip():
            records.append('=LDR' + part)
    
    if not records:
        return input_text
    
    # Get template 852 field
    template_852 = get_852_field_template(records)
    if not template_852:
        return input_text  # No 852 fields found, nothing to process
    
    # Find missing 852 fields
    missing_fields = find_missing_852_fields(records)
    
    # Add missing fields
    for record_index in sorted(missing_fields.keys()):
        records[record_index] = add_missing_852_fields(
            records[record_index], 
            missing_fields[record_index], 
            template_852
        )
    
    return '\n\n'.join(records)

def get_processing_stats(original_text, processed_text):
    """Get statistics about the processing"""
    original_records = len([r for r in original_text.split('\n\n=LDR') if r.strip()])
    processed_records = len([r for r in processed_text.split('\n\n=LDR') if r.strip()])
    
    original_852_count = len(re.findall(r'=852', original_text))
    processed_852_count = len(re.findall(r'=852', processed_text))
    
    return {
        'original_records': original_records,
        'processed_records': processed_records,
        'original_852_fields': original_852_count,
        'processed_852_fields': processed_852_count,
        'added_fields': processed_852_count - original_852_count
    }

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/process', methods=['POST'])
def process_records():
    try:
        if 'file' in request.files and request.files['file'].filename:
            # File upload
            file = request.files['file']
            if file.filename == '':
                return jsonify({'error': 'No file selected'}), 400
            
            filename = secure_filename(file.filename)
            if not filename.lower().endswith(('.txt', '.mrk')):
                return jsonify({'error': 'Please upload a .txt or .marc file'}), 400
            
            content = file.read().decode('utf-8')
        elif 'text_input' in request.form and request.form['text_input'].strip():
            # Text input
            content = request.form['text_input']
        else:
            return jsonify({'error': 'Please provide either a file or text input'}), 400
        
        # Process the content
        processed_content = process_marc_records(content)
        stats = get_processing_stats(content, processed_content)
        
        return jsonify({
            'success': True,
            'processed_content': processed_content,
            'stats': stats
        })
    
    except Exception as e:
        return jsonify({'error': f'Processing error: {str(e)}'}), 500

@app.route('/download')
def download_file():
    try:
        processed_content = request.args.get('content', '')
        if not processed_content:
            return jsonify({'error': 'No content to download'}), 400

        file_obj = io.BytesIO()
        file_obj.write(processed_content.encode('utf-8'))
        file_obj.seek(0)

        return send_file(
            file_obj,
            as_attachment=True,
            download_name='processed_marc_records.mrk',
            mimetype='text/plain'
        )

    except Exception as e:
        return jsonify({'error': f'Download error: {str(e)}'}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)