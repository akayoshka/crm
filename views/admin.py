import traceback

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from werkzeug.exceptions import NotFound
from sqlalchemy import or_

from app import db
from forms import (
    UserForm, EditUserForm, CompanyForm, AdminRegistrationForm,
    CompanyOwnerRegistrationForm, SearchForm
)
from models import User, Company, UserRole, Admin, Log, ActionType
from services import UserService, LogService, CompanyService
from utils import admin_required, log_action

admin = Blueprint('admin', __name__, url_prefix='/admin')


@admin.route('/')
@admin_required
def dashboard():
    user_count = User.query.count()
    admin_count = Admin.query.count()
    company_count = Company.query.count()
    recent_logs = LogService.get_recent_logs(limit=10)

    return render_template(
        'admin/dashboard.html',
        title='Admin Dashboard',
        user_count=user_count,
        admin_count=admin_count,
        company_count=company_count,
        recent_logs=recent_logs
    )


@admin.route('/users')
@admin_required
def user_list():
    page = request.args.get('page', 1, type=int)
    search_form = SearchForm()
    search_term = request.args.get('search', '')

    if search_term:
        users = User.query.filter(
            or_(
                User.username.ilike(f'%{search_term}%'),
                User.email.ilike(f'%{search_term}%'),
                User.first_name.ilike(f'%{search_term}%'),
                User.last_name.ilike(f'%{search_term}%')
            )
        ).paginate(page=page, per_page=10)
    else:
        users = User.query.paginate(page=page, per_page=10)

    return render_template(
        'admin/user_list.html',
        title='User Management',
        users=users,
        search_form=search_form,
        search_term=search_term
    )


@admin.route('/users/create', methods=['GET', 'POST'])
@admin_required
def create_user():
    form = UserForm()

    if form.validate_on_submit():
        try:
            user = UserService.create_user(form, db.session)
            flash(f'User {user.username} created successfully!', 'success')
            return redirect(url_for('admin.user_list'))
        except Exception as e:
            flash(f'Error creating user: {str(e)}', 'danger')

    return render_template('admin/create_user.html', title='Create User', form=form)


@admin.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    form = EditUserForm(original_username=user.username, original_email=user.email)

    if request.method == 'GET':
        form.username.data = user.username
        form.email.data = user.email
        form.first_name.data = user.first_name
        form.last_name.data = user.last_name
        form.phone.data = user.phone
        form.role.data = user.role.value
        form.is_active.data = user.is_active

    if form.validate_on_submit():
        try:
            UserService.update_user(user, form, db.session)
            flash(f'User {user.username} updated successfully!', 'success')
            return redirect(url_for('admin.user_list'))
        except Exception as e:
            flash(f'Error updating user: {str(e)}', 'danger')

    return render_template('admin/edit_user.html', title='Edit User', form=form, user=user)


@admin.route('/users/<int:user_id>/delete', methods=['POST'])
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)

    if user.id == current_app.config.get('ADMIN_ID'):
        flash('Cannot delete the main administrator!', 'danger')
        return redirect(url_for('admin.user_list'))

    try:
        username = user.username
        db.session.delete(user)
        db.session.commit()
        log_action(ActionType.DELETE, f"Deleted user {username}", db)
        flash(f'User {username} deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting user: {str(e)}', 'danger')

    return redirect(url_for('admin.user_list'))


@admin.route('/users/<int:user_id>/view')
@admin_required
def view_user(user_id):
    user = User.query.get_or_404(user_id)
    recent_logs = LogService.get_user_logs(user_id, limit=10)

    return render_template(
        'admin/view_user.html',
        title=f'User: {user.username}',
        user=user,
        recent_logs=recent_logs
    )


@admin.route('/admins/create', methods=['GET', 'POST'])
@admin_required
def create_admin():
    """
    Create a new administrator user
    Fixed to explicitly set the role field in the form
    """
    form = AdminRegistrationForm()
    current_app.logger.info("Rendering admin creation form")

    # Explicitly set role field for admin
    form.role.data = UserRole.ADMIN.value

    if form.validate_on_submit():
        current_app.logger.info(f"Form validated, attempting to create admin with username: {form.username.data}")
        try:
            # Use UserService to create admin
            user = UserService.create_admin(form, db.session)
            flash(f'Admin {user.username} created successfully!', 'success')
            return redirect(url_for('admin.user_list'))
        except Exception as e:
            error_details = traceback.format_exc()
            current_app.logger.error(f"Error creating admin: {str(e)}")
            current_app.logger.error(f"Error details: {error_details}")
            flash(f'Error creating admin: {str(e)}', 'danger')

    # If GET request or form validation failed
    return render_template('admin/create_admin.html', title='Create Admin', form=form)


@admin.route('/companies')
@admin_required
def company_list():
    page = request.args.get('page', 1, type=int)
    search_form = SearchForm()
    search_term = request.args.get('search', '')

    if search_term:
        companies = Company.query.filter(
            or_(
                Company.name.ilike(f'%{search_term}%'),
                Company.legal_name.ilike(f'%{search_term}%'),
                Company.tax_id.ilike(f'%{search_term}%'),
                Company.email.ilike(f'%{search_term}%')
            )
        ).paginate(page=page, per_page=10)
    else:
        companies = Company.query.paginate(page=page, per_page=10)

    return render_template(
        'admin/company_list.html',
        title='Company Management',
        companies=companies,
        search_form=search_form,
        search_term=search_term
    )


@admin.route('/companies/create', methods=['GET', 'POST'])
@admin_required
def create_company():
    form = CompanyForm()

    if form.validate_on_submit():
        try:
            company = CompanyService.create_company(form, db.session)
            flash(f'Company {company.name} created successfully!', 'success')
            return redirect(url_for('admin.company_list'))
        except Exception as e:
            flash(f'Error creating company: {str(e)}', 'danger')

    return render_template('admin/create_company.html', title='Create Company', form=form)


@admin.route('/companies/<int:company_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_company(company_id):
    company = Company.query.get_or_404(company_id)
    form = CompanyForm()

    if request.method == 'GET':
        form.name.data = company.name
        form.legal_name.data = company.legal_name
        form.tax_id.data = company.tax_id
        form.address.data = company.address
        form.phone.data = company.phone
        form.email.data = company.email
        form.website.data = company.website

    if form.validate_on_submit():
        try:
            CompanyService.update_company(company, form, db.session)
            flash(f'Company {company.name} updated successfully!', 'success')
            return redirect(url_for('admin.company_list'))
        except Exception as e:
            flash(f'Error updating company: {str(e)}', 'danger')

    return render_template('admin/edit_company.html', title='Edit Company', form=form, company=company)


@admin.route('/companies/<int:company_id>/delete', methods=['POST'])
@admin_required
def delete_company(company_id):
    company = Company.query.get_or_404(company_id)

    try:
        company_name = company.name
        db.session.delete(company)
        db.session.commit()
        log_action(ActionType.DELETE, f"Deleted company {company_name}", db)
        flash(f'Company {company_name} deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting company: {str(e)}', 'danger')

    return redirect(url_for('admin.company_list'))


@admin.route('/companies/<int:company_id>/view')
@admin_required
def view_company(company_id):
    company = Company.query.get_or_404(company_id)
    metrics = CompanyService.get_company_metrics(company, db.session)

    return render_template(
        'admin/view_company.html',
        title=f'Company: {company.name}',
        company=company,
        metrics=metrics
    )


@admin.route('/company-owners/create', methods=['GET', 'POST'])
@admin_required
def create_company_owner():
    form = CompanyOwnerRegistrationForm()

    # Populate company choices
    companies = Company.query.all()
    form.company_id.choices = [(c.id, c.name) for c in companies]
    form.company_id.choices.insert(0, (0, 'Select a company'))

    if form.validate_on_submit():
        try:
            user = UserService.create_company_owner(form, db.session)
            flash(f'Company owner {user.username} created successfully!', 'success')
            return redirect(url_for('admin.user_list'))
        except Exception as e:
            flash(f'Error creating company owner: {str(e)}', 'danger')

    return render_template('admin/create_company_owner.html', title='Create Company Owner', form=form)


@admin.route('/logs')
@admin_required
def log_list():
    page = request.args.get('page', 1, type=int)
    logs = Log.query.order_by(Log.timestamp.desc()).paginate(page=page, per_page=20)

    return render_template(
        'admin/logs.html',
        title='System Logs',
        logs=logs
    )