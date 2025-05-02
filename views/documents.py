import traceback
from operator import or_

from flask import Blueprint, render_template, redirect, url_for, flash, request, send_file, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
import os
from datetime import datetime
from utils import log_action

from app import db
from models import Document, User, UserRole, Task, TaskStatus, Route, RouteStatus, ActionType, Company, DocumentCategory
from forms import DocumentUploadForm, DocumentSearchForm
from utils import role_required, company_access_required, log_action, save_document, delete_file

documents = Blueprint('documents', __name__, url_prefix='/documents')


@documents.route('/')
@login_required
def list_documents():
    """
    List all documents with filtering options
    """
    page = request.args.get('page', 1, type=int)
    search_term = request.args.get('search', '')
    task_id = request.args.get('task_id', None, type=int)
    category = request.args.get('category', None)

    # Get company ID based on user role
    company_id = None
    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner:
        company_id = current_user.company_owner.company_id
    elif current_user.role == UserRole.MANAGER and current_user.manager:
        company_id = current_user.manager.company_id
    elif current_user.role == UserRole.OPERATOR and current_user.operator:
        company_id = current_user.operator.company_id
    elif current_user.role == UserRole.DRIVER and current_user.driver:
        company_id = current_user.driver.company_id

    # For admins, they can filter by company
    if current_user.role == UserRole.ADMIN:
        company_id = request.args.get('company_id', None, type=int)

    # Build query
    doc_query = Document.query

    # Apply company filter
    if company_id:
        doc_query = doc_query.filter(Document.company_id == company_id)

    # Apply task filter
    if task_id:
        doc_query = doc_query.filter(Document.task_id == task_id)

    # Apply category filter
    if category:
        try:
            # Fix: Convert to enum but use value for database filtering
            doc_category = DocumentCategory(category.lower())
            doc_query = doc_query.filter(Document.document_category.has(value=doc_category.value))
        except ValueError:
            # Invalid category, ignore filter
            pass

    # Apply search filter
    if search_term:
        doc_query = doc_query.filter(Document.title.ilike(f'%{search_term}%'))

    # Order by upload time (newest first)
    doc_query = doc_query.order_by(Document.uploaded_at.desc())

    # Paginate results
    documents = doc_query.paginate(page=page, per_page=10)

    # Get search form
    search_form = DocumentSearchForm()

    # Get document categories for sidebar
    categories = [(c.value, c.name) for c in DocumentCategory]

    log_action(ActionType.VIEW, "Viewed documents list", db)

    return render_template(
        'documents/list_documents.html',
        title='Documents',
        documents=documents,
        search_term=search_term,
        task_id=task_id,
        company_id=company_id,
        search_form=search_form,
        categories=categories,
        current_category=category
    )


@documents.route('/upload', methods=['GET', 'POST'])
@login_required
def upload_document():
    """
    Upload a new document
    """
    form = DocumentUploadForm()

    if form.validate_on_submit():
        try:
            # Get company ID based on user role
            company_id = None
            if current_user.company_owner:
                company_id = current_user.company_owner.company_id
            elif current_user.manager:
                company_id = current_user.manager.company_id
            elif current_user.operator:
                company_id = current_user.operator.company_id
            elif current_user.driver:
                company_id = current_user.driver.company_id

            # For admins, they can select a company
            if current_user.role.value == 'admin':
                company_id = form.company_id.data if hasattr(form, 'company_id') else None

            # Validate company existence
            if not company_id:
                error_msg = 'Company ID is required for document upload.'
                current_app.logger.warning(f"Document upload failed: {error_msg} User: {current_user.username} (ID: {current_user.id})")
                flash(error_msg, 'danger')
                return redirect(url_for('documents.list_documents'))

            # Get form data
            title = form.title.data
            document_file = form.document.data
            task_id = request.form.get('task_id')
            route_id = request.form.get('route_id')
            document_category = request.form.get('document_category', 'other')  # Default to 'other'

            # Convert task_id to int or None
            task_id = int(task_id) if task_id and task_id.isdigit() else None

            # Convert route_id to int or None
            route_id = int(route_id) if route_id and route_id.isdigit() else None

            # Set document category based on task/route
            if task_id:
                document_category = 'task'
            elif route_id:
                document_category = 'route'

            # Convert string category to enum
            try:
                doc_category = DocumentCategory(document_category.lower())
            except ValueError:
                error_msg = f"Invalid document category: {document_category}"
                current_app.logger.warning(f"Document upload warning: {error_msg}. Using default category 'OTHER'")
                doc_category = DocumentCategory.OTHER

            # Log the document upload attempt
            current_app.logger.info(
                f"Document upload attempt: Title: '{title}', Company: {company_id}, Task: {task_id}, Route: {route_id}, Category: {document_category}, User: {current_user.username} (ID: {current_user.id})"
            )

            # Save document and get file information
            file_path, file_type, file_size = save_document(document_file, task_id, company_id)

            if not file_path:
                error_msg = 'Error saving document. Please check file type and size.'
                current_app.logger.error(
                    f"Document upload failed: {error_msg} File: '{document_file.filename}', User: {current_user.username} (ID: {current_user.id})"
                )
                flash(error_msg, 'danger')
                return redirect(url_for('documents.upload_document'))

            # Create document record
            document = Document(
                title=title,
                file_path=file_path,
                file_type=file_type,
                size=file_size,
                uploader_id=current_user.id,
                task_id=task_id,
                route_id=route_id,
                company_id=company_id,
                document_category=doc_category,
                uploaded_at=datetime.utcnow()
            )

            db.session.add(document)
            db.session.commit()

            # Log successful upload
            current_app.logger.info(
                f"Document uploaded successfully: Title: '{title}', Type: {file_type}, Size: {file_size} bytes, Path: {file_path}, User: {current_user.username} (ID: {current_user.id})"
            )
            log_action(ActionType.CREATE, f"Uploaded document: {title}", db)
            flash('Document uploaded successfully!', 'success')

            # Redirect to document list, with task filter if applicable
            if task_id:
                return redirect(url_for('documents.list_documents', task_id=task_id))
            elif route_id:
                return redirect(url_for('documents.list_documents', route_id=route_id))
            else:
                return redirect(url_for('documents.list_documents', category=document_category))

        except Exception as e:
            db.session.rollback()
            # Enhanced error logging with full traceback
            error_trace = traceback.format_exc()
            current_app.logger.error(f"Document upload error: {str(e)}\nUser: {current_user.username} (ID: {current_user.id})\nTraceback: {error_trace}")
            flash(f'Error uploading document: {str(e)}', 'danger')

    return render_template(
        'documents/upload_document.html',
        title='Upload Document',
        form=form
    )


@documents.route('/<int:document_id>')
@login_required
def view_document(document_id):
    """
    View document details
    """
    document = Document.query.get_or_404(document_id)

    # Check if user has access to this document
    if not _can_access_document(document):
        flash('You do not have permission to access this document.', 'danger')
        return redirect(url_for('documents.list_documents'))

    log_action(ActionType.VIEW, f"Viewed document: {document.title}", db)

    return render_template(
        'documents/view_document.html',
        title=f'Document: {document.title}',
        document=document
    )


@documents.route('/<int:document_id>/download')
@login_required
def download_document(document_id):
    """
    Download a document
    """
    document = Document.query.get_or_404(document_id)

    # Check if user has access to this document
    if not _can_access_document(document):
        flash('You do not have permission to access this document.', 'danger')
        return redirect(url_for('documents.list_documents'))

    # Get the full path to the file
    if document.file_path.startswith('uploads/'):
        relative_path = document.file_path[len('uploads/'):]
    else:
        relative_path = document.file_path
    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], relative_path)
    if not os.path.exists(file_path):
        flash('Document file not found. Please contact an administrator.', 'danger')
        return redirect(url_for('documents.view_document', document_id=document_id))

    # Log the download
    log_action(ActionType.DOWNLOAD, f"Downloaded document: {document.title}", db)

    # Get filename from document title and file type
    filename = secure_filename(f"{document.title}.{document.file_type}")

    return send_file(
        file_path,
        as_attachment=True,
        download_name=filename
    )


@documents.route('/<int:document_id>/delete', methods=['POST'])
@login_required
def delete_document(document_id):
    """
    Delete a document
    """
    document = Document.query.get_or_404(document_id)

    # Check if user has permission to delete this document
    if not _can_delete_document(document):
        unauthorized_msg = 'You do not have permission to delete this document.'
        current_app.logger.warning(
            f"Unauthorized document deletion attempt: Document: '{document.title}' (ID: {document.id}), User: {current_user.username} (ID: {current_user.id})"
        )
        flash(unauthorized_msg, 'danger')
        return redirect(url_for('documents.view_document', document_id=document_id))

    try:
        # Get the task ID and route ID for redirect after delete
        task_id = document.task_id
        route_id = document.route_id

        # Fix: handle document_category properly whether it's a string or enum
        category = None
        if document.document_category:
            # If it's already a string, use it directly
            if isinstance(document.document_category, str):
                category = document.document_category
            # If it's an enum, get its value
            elif hasattr(document.document_category, 'value'):
                category = document.document_category.value
            # If it can be cast to string, do that
            else:
                category = str(document.document_category)

        # Log deletion attempt
        current_app.logger.info(
            f"Document deletion attempt: Title: '{document.title}' (ID: {document.id}), Type: {document.file_type}, Path: {document.file_path}, User: {current_user.username} (ID: {current_user.id})"
        )

        # Delete the file from storage
        delete_result = delete_file(document.file_path)
        if not delete_result:
            # Log file deletion failure but proceed with database deletion
            current_app.logger.warning(
                f"File deletion failed but proceeding with database record deletion: Path: {document.file_path}, Document: '{document.title}' (ID: {document.id}), User: {current_user.username} (ID: {current_user.id})"
            )

        # Delete the database record
        title = document.title
        db.session.delete(document)
        db.session.commit()

        # Log successful deletion
        current_app.logger.info(
            f"Document deleted successfully: Title: '{title}' (ID: {document_id}), User: {current_user.username} (ID: {current_user.id})"
        )
        log_action(ActionType.DELETE, f"Deleted document: {title}", db)
        flash(f'Document "{title}" deleted successfully!', 'success')

        # Redirect to the task if this was a task document
        if task_id:
            return redirect(url_for('tasks.view_task', task_id=task_id))
        elif route_id:
            return redirect(url_for('routes.view_route', route_id=route_id))
        else:
            return redirect(url_for('documents.list_documents', category=category))

    except Exception as e:
        db.session.rollback()
        # Enhanced error logging with full traceback
        error_trace = traceback.format_exc()
        current_app.logger.error(
            f"Document deletion error: {str(e)}\nDocument: '{document.title}' (ID: {document.id}), User: {current_user.username} (ID: {current_user.id})\nTraceback: {error_trace}"
        )
        flash(f'Error deleting document: {str(e)}', 'danger')
        return redirect(url_for('documents.view_document', document_id=document_id))


@documents.route('/<int:document_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_document(document_id):
    """
    Edit document metadata
    """
    document = Document.query.get_or_404(document_id)

    # Check if user has permission to edit this document
    if not _can_edit_document(document):
        unauthorized_msg = 'You do not have permission to edit this document.'
        current_app.logger.warning(
            f"Unauthorized document edit attempt: Document: '{document.title}' (ID: {document.id}), User: {current_user.username} (ID: {current_user.id})"
        )
        flash(unauthorized_msg, 'danger')
        return redirect(url_for('documents.view_document', document_id=document_id))

    # Create form
    form = DocumentUploadForm()

    # Remove document upload field - we're just editing metadata
    delattr(form, 'document')

    # Get company ID based on user role
    company_id = None
    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner:
        company_id = current_user.company_owner.company_id
    elif current_user.role == UserRole.MANAGER and current_user.manager:
        company_id = current_user.manager.company_id
    elif current_user.role == UserRole.OPERATOR and current_user.operator:
        company_id = current_user.operator.company_id
    elif current_user.role == UserRole.DRIVER and current_user.driver:
        company_id = current_user.driver.company_id

    # For admins, they can select a company
    if current_user.role == UserRole.ADMIN:
        # Get all companies for dropdown
        all_companies = Company.query.all()
        form.company_id.choices = [(c.id, c.name) for c in all_companies]
    else:
        # Non-admins can only assign to their company
        if not company_id:
            error_msg = 'You are not associated with a company.'
            current_app.logger.warning(
                f"Document edit failed: {error_msg} User: {current_user.username} (ID: {current_user.id})"
            )
            flash(error_msg, 'danger')
            return redirect(url_for('documents.view_document', document_id=document_id))

        # Hide company field for non-admins
        if hasattr(form, 'company_id'):
            del form.company_id

    # Get available tasks for dropdown
    if company_id:
        task_query = Task.query.filter_by(company_id=company_id)

        # For drivers, only show their tasks
        if current_user.role == UserRole.DRIVER:
            task_query = task_query.filter_by(assignee_id=current_user.id)

        tasks = task_query.all()

        # Update task choices
        if hasattr(form, 'task_id'):
            form.task_id.choices = [(t.id, t.title) for t in tasks]
            form.task_id.choices.insert(0, (0, 'No task'))

    # For GET requests, fill form with current data
    if request.method == 'GET':
        form.title.data = document.title
        if hasattr(form, 'task_id'):
            form.task_id.data = document.task_id if document.task_id else 0
        if hasattr(form, 'company_id'):
            form.company_id.data = document.company_id

    if form.validate_on_submit():
        try:
            # Log edit attempt
            current_app.logger.info(
                f"Document edit attempt: Title: '{document.title}' (ID: {document.id}), User: {current_user.username} (ID: {current_user.id})"
            )

            # Track changes for logging
            changes = []

            # Update document metadata
            if document.title != form.title.data:
                old_title = document.title
                document.title = form.title.data
                changes.append(f"Title: '{old_title}' → '{document.title}'")

            # Update task if changed
            if hasattr(form, 'task_id'):
                new_task_id = form.task_id.data if form.task_id.data != 0 else None
                if new_task_id != document.task_id:
                    old_task_id = document.task_id
                    document.task_id = new_task_id
                    changes.append(f"Task ID: {old_task_id} → {document.task_id}")

                    if new_task_id:
                        old_category = document.document_category.value if document.document_category else None
                        document.document_category = DocumentCategory.TASK.value
                        changes.append(f"Category: {old_category} → {document.document_category.value}")
                    elif document.document_category == DocumentCategory.TASK.value:
                        old_category = document.document_category.value
                        document.document_category = DocumentCategory.OTHER.value
                        changes.append(f"Category: {old_category} → {document.document_category.value}")

            # Update company if allowed and changed
            if hasattr(form, 'company_id') and form.company_id.data != document.company_id:
                old_company_id = document.company_id
                document.company_id = form.company_id.data
                changes.append(f"Company ID: {old_company_id} → {document.company_id}")

            # Update document category if provided
            category = request.form.get('document_category')
            if category:
                try:
                    old_category = document.document_category.value if document.document_category else None
                    # Fix: Use enum properly
                    new_category = DocumentCategory(category.lower())
                    document.document_category = new_category
                    if old_category != new_category.value:
                        changes.append(f"Category: {old_category} → {new_category.value}")
                except ValueError:
                    current_app.logger.warning(
                        f"Invalid document category provided: {category}. Using default category 'OTHER'. User: {current_user.username} (ID: {current_user.id})"
                    )
                    old_category = document.document_category.value if document.document_category else None
                    document.document_category = DocumentCategory.OTHER.value
                    if old_category != DocumentCategory.OTHER.value:
                        changes.append(f"Category: {old_category} → {DocumentCategory.OTHER.value}")

            db.session.commit()

            # Log successful edit with details of changes
            changes_str = ', '.join(changes) if changes else 'No changes made'
            current_app.logger.info(
                f"Document updated successfully: ID: {document.id}, {changes_str}, User: {current_user.username} (ID: {current_user.id})"
            )
            log_action(ActionType.UPDATE, f"Updated document: {document.title}", db)
            flash('Document updated successfully!', 'success')

            return redirect(url_for('documents.view_document', document_id=document_id))

        except Exception as e:
            db.session.rollback()
            # Enhanced error logging with full traceback
            error_trace = traceback.format_exc()
            current_app.logger.error(
                f"Document edit error: {str(e)}\nDocument: '{document.title}' (ID: {document.id}), User: {current_user.username} (ID: {current_user.id})\nTraceback: {error_trace}"
            )
            flash(f'Error updating document: {str(e)}', 'danger')

    return render_template(
        'documents/edit_document.html',
        title=f'Edit Document: {document.title}',
        form=form,
        document=document,
        document_categories=[(c.value, c.name) for c in DocumentCategory]
    )


@documents.route('/by-task/<int:task_id>')
@login_required
def task_documents(task_id):
    """
    Show documents for a specific task
    """
    task = Task.query.get_or_404(task_id)

    # Check if user can access this task
    company_id = None
    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner:
        company_id = current_user.company_owner.company_id
    elif current_user.role == UserRole.MANAGER and current_user.manager:
        company_id = current_user.manager.company_id
    elif current_user.role == UserRole.OPERATOR and current_user.operator:
        company_id = current_user.operator.company_id
    elif current_user.role == UserRole.DRIVER and current_user.driver:
        company_id = current_user.driver.company_id

    # Check if user has access to this task's company
    if current_user.role != UserRole.ADMIN and (not company_id or task.company_id != company_id):
        # Special case: driver can access their assigned tasks
        if not (current_user.role == UserRole.DRIVER and task.assignee_id == current_user.id):
            flash('You do not have access to this task.', 'danger')
            return redirect(url_for('documents.list_documents'))

    # Get task documents
    page = request.args.get('page', 1, type=int)
    documents = Document.query.filter_by(task_id=task_id).order_by(Document.uploaded_at.desc()).paginate(page=page,
                                                                                                         per_page=10)

    log_action(ActionType.VIEW, f"Viewed documents for task: {task.title}", db)

    return render_template(
        'documents/task_documents.html',
        title=f'Documents for Task: {task.title}',
        documents=documents,
        task=task
    )


@documents.route('/by-route/<int:route_id>')
@login_required
def route_documents(route_id):
    """
    Show documents for a specific route
    """
    route = Route.query.get_or_404(route_id)

    # Check if user can access this route
    company_id = None
    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner:
        company_id = current_user.company_owner.company_id
    elif current_user.role == UserRole.MANAGER and current_user.manager:
        company_id = current_user.manager.company_id
    elif current_user.role == UserRole.OPERATOR and current_user.operator:
        company_id = current_user.operator.company_id
    elif current_user.role == UserRole.DRIVER and current_user.driver:
        company_id = current_user.driver.company_id

    # Check if user has access to this route's company
    if current_user.role != UserRole.ADMIN and (not company_id or route.company_id != company_id):
        # Special case: driver can access their assigned routes
        if not (current_user.role == UserRole.DRIVER and route.driver_id == current_user.driver.id):
            flash('You do not have access to this route.', 'danger')
            return redirect(url_for('documents.list_documents'))

    # Get route documents
    page = request.args.get('page', 1, type=int)
    documents = Document.query.filter_by(route_id=route_id).order_by(Document.uploaded_at.desc()).paginate(page=page,
                                                                                                           per_page=10)

    log_action(ActionType.VIEW, f"Viewed documents for route: {route.id}", db)

    return render_template(
        'documents/route_documents.html',
        title=f'Documents for Route: {route.start_point} to {route.end_point}',
        documents=documents,
        route=route
    )


@documents.route('/by-category/<category>')
@login_required
def category_documents(category):
    """
    Show documents by category
    """
    page = request.args.get('page', 1, type=int)

    # Validate category
    try:
        doc_category = DocumentCategory(category.lower())
    except ValueError:
        flash('Invalid document category.', 'danger')
        return redirect(url_for('documents.list_documents'))

    # Get company ID based on user role
    company_id = None
    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner:
        company_id = current_user.company_owner.company_id
    elif current_user.role == UserRole.MANAGER and current_user.manager:
        company_id = current_user.manager.company_id
    elif current_user.role == UserRole.OPERATOR and current_user.operator:
        company_id = current_user.operator.company_id
    elif current_user.role == UserRole.DRIVER and current_user.driver:
        company_id = current_user.driver.company_id

    # Build query
    # Fix: Use value for database filtering
    doc_query = Document.query.filter(Document.document_category.has(value=doc_category.value))

    # Apply company filter if not admin
    if current_user.role != UserRole.ADMIN and company_id:
        doc_query = doc_query.filter(Document.company_id == company_id)

    # For drivers, only show documents they have access to
    if current_user.role == UserRole.DRIVER:
        task_ids = [task.id for task in Task.query.filter_by(assignee_id=current_user.id).all()]
        route_ids = [route.id for route in Route.query.filter_by(driver_id=current_user.driver.id).all()]

        doc_query = doc_query.filter(
            or_(
                Document.uploader_id == current_user.id,
                Document.task_id.in_(task_ids) if task_ids else False,
                Document.route_id.in_(route_ids) if route_ids else False,
                Document.access_user_id == current_user.id
            )
        )

    # Order by upload time (newest first)
    doc_query = doc_query.order_by(Document.uploaded_at.desc())

    # Paginate results
    documents = doc_query.paginate(page=page, per_page=10)

    log_action(ActionType.VIEW, f"Viewed documents by category: {category}", db)

    return render_template(
        'documents/category_documents.html',
        title=f'Documents: {doc_category.name}',
        documents=documents,
        category=category,
        category_name=doc_category.name
    )


@documents.route('/search', methods=['GET', 'POST'])
@login_required
def search_documents():
    """
    Advanced search for documents
    """
    form = DocumentSearchForm()

    # Get company ID based on user role
    company_id = None
    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner:
        company_id = current_user.company_owner.company_id
    elif current_user.role == UserRole.MANAGER and current_user.manager:
        company_id = current_user.manager.company_id
    elif current_user.role == UserRole.OPERATOR and current_user.operator:
        company_id = current_user.operator.company_id
    elif current_user.role == UserRole.DRIVER and current_user.driver:
        company_id = current_user.driver.company_id

    # For admins, they can filter by company
    if current_user.role == UserRole.ADMIN:
        # Get all companies for dropdown
        all_companies = Company.query.all()
        form.company_id.choices = [(c.id, c.name) for c in all_companies]
        form.company_id.choices.insert(0, (0, 'All Companies'))
    else:
        # Non-admins can only search in their company
        if hasattr(form, 'company_id'):
            del form.company_id

    # Get available file types for dropdown
    file_types = db.session.query(Document.file_type).distinct().all()
    form.file_type.choices = [(ft[0], ft[0].upper()) for ft in file_types]
    form.file_type.choices.insert(0, ('', 'All Types'))

    # Add document categories dropdown
    categories = [(c.value, c.name) for c in DocumentCategory]
    categories.insert(0, ('', 'All Categories'))

    # Initialize results
    results = []
    searched = False

    if form.validate_on_submit() or request.args.get('search'):
        searched = True

        # Build search query
        search_query = Document.query

        # Apply company filter
        if hasattr(form, 'company_id') and form.company_id.data != 0:
            search_query = search_query.filter(Document.company_id == form.company_id.data)
        elif not hasattr(form, 'company_id') and company_id:
            search_query = search_query.filter(Document.company_id == company_id)

        # Apply title filter
        if form.title.data:
            search_query = search_query.filter(Document.title.ilike(f'%{form.title.data}%'))

        # Apply file type filter
        if form.file_type.data:
            search_query = search_query.filter(Document.file_type == form.file_type.data)

        # Apply category filter
        category = request.form.get('category', '')
        if category:
            try:
                # Fix: Use value for database filtering
                doc_category = DocumentCategory(category.lower())
                search_query = search_query.filter(Document.document_category.has(value=doc_category.value))
            except ValueError:
                # Invalid category, ignore
                pass

        # Apply date range filters
        if form.date_from.data:
            search_query = search_query.filter(Document.uploaded_at >= form.date_from.data)

        if form.date_to.data:
            # Set time to end of day
            end_date = form.date_to.data.replace(hour=23, minute=59, second=59)
            search_query = search_query.filter(Document.uploaded_at <= end_date)

        # Apply uploader filter if admin/manager/owner
        if hasattr(form, 'uploader_id') and form.uploader_id.data and form.uploader_id.data != 0:
            search_query = search_query.filter(Document.uploader_id == form.uploader_id.data)

        # For drivers, only show documents they have access to
        if current_user.role == UserRole.DRIVER:
            task_ids = [task.id for task in Task.query.filter_by(assignee_id=current_user.id).all()]
            route_ids = [route.id for route in Route.query.filter_by(driver_id=current_user.driver.id).all()]

            search_query = search_query.filter(
                or_(
                    Document.uploader_id == current_user.id,
                    Document.task_id.in_(task_ids) if task_ids else False,
                    Document.route_id.in_(route_ids) if route_ids else False,
                    Document.access_user_id == current_user.id
                )
            )

        # Order by upload time (newest first)
        search_query = search_query.order_by(Document.uploaded_at.desc())

        # Execute query
        results = search_query.all()

        log_action(ActionType.VIEW, "Performed document search", db)

    # Get uploaders list for dropdown (for admins/managers/owners)
    uploaders = []
    if current_user.role in [UserRole.ADMIN, UserRole.COMPANY_OWNER, UserRole.MANAGER] and company_id:
        uploader_query = db.session.query(
            Document.uploader_id, User.first_name, User.last_name
        ).join(
            User, Document.uploader_id == User.id
        )

        if company_id:
            uploader_query = uploader_query.filter(Document.company_id == company_id)

        uploaders = uploader_query.distinct().all()

        if hasattr(form, 'uploader_id'):
            form.uploader_id.choices = [(u[0], f"{u[1]} {u[2]}") for u in uploaders]
            form.uploader_id.choices.insert(0, (0, 'All Uploaders'))

    return render_template(
        'documents/search_documents.html',
        title='Document Search',
        form=form,
        results=results,
        searched=searched,
        categories=categories
    )


# Helper functions
def _can_access_document(document):
    """
    Check if current user can access a document
    """
    # Admin can access any document
    if current_user.role == UserRole.ADMIN:
        return True

    # Get company ID based on user role
    company_id = None
    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner:
        company_id = current_user.company_owner.company_id
    elif current_user.role == UserRole.MANAGER and current_user.manager:
        company_id = current_user.manager.company_id
    elif current_user.role == UserRole.OPERATOR and current_user.operator:
        company_id = current_user.operator.company_id
    elif current_user.role == UserRole.DRIVER and current_user.driver:
        company_id = current_user.driver.company_id

    # Must be in same company
    if document.company_id != company_id:
        # Special case: driver can access documents for their assigned tasks
        if current_user.role == UserRole.DRIVER:
            if document.task_id:
                task = Task.query.get(document.task_id)
                if task and task.assignee_id == current_user.id:
                    return True
            # Driver can access documents for their routes
            if document.route_id:
                route = Route.query.get(document.route_id)
                if route and route.driver_id == current_user.driver.id:
                    return True
            # Driver can access documents specifically assigned to them
            if document.access_user_id == current_user.id:
                return True
        return False

    return True


def _can_edit_document(document):
    """
    Check if current user can edit a document
    """
    # Admin can edit any document
    if current_user.role == UserRole.ADMIN:
        return True

    # Document uploader can edit
    if document.uploader_id == current_user.id:
        return True

    # Company owner and manager can edit company documents
    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner and document.company_id == current_user.company_owner.company_id:
        return True

    if current_user.role == UserRole.MANAGER and current_user.manager and document.company_id == current_user.manager.company_id:
        return True

    return False


def _can_delete_document(document):
    """
    Check if current user can delete a document
    """
    # Admin can delete any document
    if current_user.role == UserRole.ADMIN:
        return True

    # Document uploader can delete
    if document.uploader_id == current_user.id:
        return True

    # Company owner and manager can delete company documents
    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner and document.company_id == current_user.company_owner.company_id:
        return True

    if current_user.role == UserRole.MANAGER and current_user.manager and document.company_id == current_user.manager.company_id:
        return True

    return False