from flask_wtf import FlaskForm
from wtforms import (
    StringField, PasswordField, BooleanField, SubmitField,
    SelectField, TextAreaField, FileField, DateTimeField,
    IntegerField, FloatField, DateField, HiddenField
)
from wtforms.validators import (
    DataRequired, Email, EqualTo, Length,
    ValidationError, Optional, Regexp
)
from flask_wtf.file import FileAllowed

from models import User, UserRole, RouteStatus, TaskStatus


class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember_me = BooleanField('Remember Me')
    submit = SubmitField('Log In')


class RequestPasswordResetForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    submit = SubmitField('Request Password Reset')

    def validate_email(self, email):
        user = User.query.filter_by(email=email.data).first()
        if not user:
            raise ValidationError('There is no account with that email. You must register first.')


class ResetPasswordForm(FlaskForm):
    password = PasswordField('New Password', validators=[
        DataRequired(),
        Length(min=8, message="Password must be at least 8 characters long")
    ])
    password2 = PasswordField('Confirm Password', validators=[
        DataRequired(),
        EqualTo('password', message='Passwords must match')
    ])
    submit = SubmitField('Reset Password')


class UserForm(FlaskForm):
    username = StringField('Username', validators=[
        DataRequired(),
        Length(min=3, max=64),
        Regexp('^[A-Za-z0-9_.]+$', message='Username can only contain letters, numbers, dots and underscores')
    ])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[
        DataRequired(),
        Length(min=8, message="Password must be at least 8 characters long")
    ])
    password2 = PasswordField('Confirm Password', validators=[
        DataRequired(),
        EqualTo('password', message='Passwords must match')
    ])
    first_name = StringField('First Name', validators=[DataRequired(), Length(max=64)])
    last_name = StringField('Last Name', validators=[DataRequired(), Length(max=64)])
    phone = StringField('Phone', validators=[Length(max=20)])
    role = SelectField('Role', validators=[DataRequired()], choices=[
        (UserRole.ADMIN.value, 'Administrator'),
        (UserRole.COMPANY_OWNER.value, 'Company Owner'),
        (UserRole.MANAGER.value, 'Manager'),
        (UserRole.OPERATOR.value, 'Operator'),
        (UserRole.DRIVER.value, 'Driver')
    ])
    is_active = BooleanField('Active Account', default=True)
    profile_image = FileField('Profile Image', validators=[
        FileAllowed(['jpg', 'png', 'jpeg'], 'Images only!')
    ])
    submit = SubmitField('Create User')

    def validate_username(self, username):
        user = User.query.filter_by(username=username.data).first()
        if user:
            raise ValidationError('Username already in use. Please choose a different one.')

    def validate_email(self, email):
        user = User.query.filter_by(email=email.data).first()
        if user:
            raise ValidationError('Email already registered. Please use a different one.')


class EditUserForm(FlaskForm):
    username = StringField('Username', validators=[
        DataRequired(),
        Length(min=3, max=64),
        Regexp('^[A-Za-z0-9_.]+$', message='Username can only contain letters, numbers, dots and underscores')
    ])
    email = StringField('Email', validators=[DataRequired(), Email()])
    first_name = StringField('First Name', validators=[DataRequired(), Length(max=64)])
    last_name = StringField('Last Name', validators=[DataRequired(), Length(max=64)])
    phone = StringField('Phone', validators=[Length(max=20)])
    role = SelectField('Role', validators=[DataRequired()])
    is_active = BooleanField('Active Account', default=True)
    profile_image = FileField('Profile Image', validators=[
        FileAllowed(['jpg', 'png', 'jpeg'], 'Images only!')
    ])
    submit = SubmitField('Update User')

    def __init__(self, original_username, original_email, *args, **kwargs):
        super(EditUserForm, self).__init__(*args, **kwargs)
        self.original_username = original_username
        self.original_email = original_email
        self.role.choices = [
            (UserRole.ADMIN.value, 'Administrator'),
            (UserRole.COMPANY_OWNER.value, 'Company Owner'),
            (UserRole.MANAGER.value, 'Manager'),
            (UserRole.OPERATOR.value, 'Operator'),
            (UserRole.DRIVER.value, 'Driver')
        ]

    def validate_username(self, username):
        if username.data != self.original_username:
            user = User.query.filter_by(username=username.data).first()
            if user:
                raise ValidationError('Username already in use. Please choose a different one.')

    def validate_email(self, email):
        if email.data != self.original_email:
            user = User.query.filter_by(email=email.data).first()
            if user:
                raise ValidationError('Email already registered. Please use a different one.')


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField('Current Password', validators=[DataRequired()])
    new_password = PasswordField('New Password', validators=[
        DataRequired(),
        Length(min=8, message="Password must be at least 8 characters long")
    ])
    confirm_password = PasswordField('Confirm New Password', validators=[
        DataRequired(),
        EqualTo('new_password', message='Passwords must match')
    ])
    submit = SubmitField('Change Password')


class AdminRegistrationForm(UserForm):
    admin_level = IntegerField('Admin Level', default=1, validators=[DataRequired()])
    submit = SubmitField('Create Admin')


class CompanyForm(FlaskForm):
    name = StringField('Company Name', validators=[DataRequired(), Length(max=128)])
    legal_name = StringField('Legal Name', validators=[DataRequired(), Length(max=256)])
    tax_id = StringField('Tax ID', validators=[DataRequired(), Length(max=64)])
    address = StringField('Address', validators=[DataRequired(), Length(max=256)])
    phone = StringField('Phone', validators=[DataRequired(), Length(max=20)])
    email = StringField('Email', validators=[DataRequired(), Email(), Length(max=120)])
    website = StringField('Website', validators=[Optional(), Length(max=128)])
    submit = SubmitField('Save Company')


class CompanyOwnerRegistrationForm(UserForm):
    company_id = SelectField('Company', coerce=int, validators=[Optional()])
    create_new_company = BooleanField('Create New Company')

    # Company fields (for new company creation)
    company_name = StringField('Company Name', validators=[Optional(), Length(max=128)])
    company_legal_name = StringField('Legal Name', validators=[Optional(), Length(max=256)])
    company_tax_id = StringField('Tax ID', validators=[Optional(), Length(max=64)])
    company_address = StringField('Address', validators=[Optional(), Length(max=256)])
    company_phone = StringField('Company Phone', validators=[Optional(), Length(max=20)])
    company_email = StringField('Company Email', validators=[Optional(), Email(), Length(max=120)])
    company_website = StringField('Company Website', validators=[Optional(), Length(max=128)])

    submit = SubmitField('Create Company Owner')

    def __init__(self, *args, **kwargs):
        super(CompanyOwnerRegistrationForm, self).__init__(*args, **kwargs)
        self.role.data = UserRole.COMPANY_OWNER.value

    def validate(self, extra_validators=None):
        # First call the parent class validate method with extra_validators
        if not super(CompanyOwnerRegistrationForm, self).validate(extra_validators=extra_validators):
            return False

        # Company validation logic
        if not self.company_id.data and not self.create_new_company.data:
            self.company_id.errors.append('Please select a company or create a new one')
            return False

        if self.create_new_company.data:
            if not self.company_name.data:
                self.company_name.errors.append('Company name is required')
                return False
            if not self.company_legal_name.data:
                self.company_legal_name.errors.append('Company legal name is required')
                return False
            if not self.company_tax_id.data:
                self.company_tax_id.errors.append('Company tax ID is required')
                return False
            if not self.company_address.data:
                self.company_address.errors.append('Company address is required')
                return False
            if not self.company_phone.data:
                self.company_phone.errors.append('Company phone is required')
                return False
            if not self.company_email.data:
                self.company_email.errors.append('Company email is required')
                return False

        return True


class SearchForm(FlaskForm):
    search = StringField('Search', validators=[DataRequired()])
    submit = SubmitField('Search')


class TaskForm(FlaskForm):
    def __init__(self, *args, **kwargs):
        super(TaskForm, self).__init__(*args, **kwargs)
        # Initialize choices for SelectField fields to prevent 'Choices cannot be None' error
        if hasattr(self, 'status') and self.status.choices is None:
            self.status.choices = [(s.value, s.name) for s in TaskStatus]
        if hasattr(self, 'assignee_id') and self.assignee_id.choices is None:
            self.assignee_id.choices = []
        if hasattr(self, 'company_id') and self.company_id.choices is None:
            self.company_id.choices = []

    title = StringField('Title', validators=[DataRequired(), Length(max=128)])
    description = TextAreaField('Description', validators=[Optional()])
    assignee_id = SelectField('Assign To Driver', coerce=int, validators=[Optional()])
    company_id = SelectField('Company', coerce=int, validators=[Optional()])
    deadline = DateTimeField('Deadline', format='%Y-%m-%dT%H:%M', validators=[Optional()])
    status = SelectField('Status', validators=[Optional()], choices=[
        (TaskStatus.NEW.value, 'New'),
        (TaskStatus.IN_PROGRESS.value, 'In Progress'),
        (TaskStatus.ON_HOLD.value, 'On Hold'),
        (TaskStatus.COMPLETED.value, 'Completed'),
        (TaskStatus.CANCELLED.value, 'Cancelled')
    ])
    document = FileField('Attach Document', validators=[
        Optional(),
        FileAllowed(['pdf', 'doc', 'docx', 'xlsx', 'xls', 'txt', 'jpg', 'jpeg', 'png'], 'Supported file types: PDF, Word, Excel, Text, Images')
    ])
    submit = SubmitField('Save Task')


class DocumentUploadForm(FlaskForm):
    title = StringField('Document Title', validators=[DataRequired(), Length(max=128)])
    document = FileField('Document File', validators=[
        DataRequired(),
        FileAllowed(['pdf', 'doc', 'docx', 'xlsx', 'xls', 'txt', 'jpg', 'jpeg', 'png'], 'Supported file types: PDF, Word, Excel, Text, Images')
    ])
    submit = SubmitField('Upload Document')


class MessageForm(FlaskForm):
    content = TextAreaField('Message', validators=[DataRequired(), Length(max=1000)])
    task_id = HiddenField('Task ID', filters=[lambda x: int(x) if x and x.isdigit() else None])
    submit = SubmitField('Send')


class RouteForm(FlaskForm):
    def __init__(self, *args, **kwargs):
        super(RouteForm, self).__init__(*args, **kwargs)
        # Initialize choices for SelectField fields
        if hasattr(self, 'status') and self.status.choices is None:
            self.status.choices = [(s.value, s.name) for s in RouteStatus]
        if hasattr(self, 'driver_id') and self.driver_id.choices is None:
            self.driver_id.choices = []

    start_point = StringField('Start Point', validators=[DataRequired(), Length(max=256)])
    end_point = StringField('End Point', validators=[DataRequired(), Length(max=256)])
    driver_id = SelectField('Driver', coerce=int, validators=[DataRequired()])
    task_id = SelectField('Related Task', coerce=int, validators=[Optional()])
    waypoints = TextAreaField('Waypoints', validators=[Optional()],
                             description="Optional waypoints in JSON format")
    distance = FloatField('Distance (km)', validators=[Optional()])
    estimated_time = IntegerField('Estimated Time (minutes)', validators=[Optional()])
    start_time = DateTimeField('Start Time', format='%Y-%m-%dT%H:%M', validators=[Optional()])
    status = SelectField('Status', validators=[Optional()], choices=[
        (RouteStatus.PLANNED.value, 'Planned'),
        (RouteStatus.IN_PROGRESS.value, 'In Progress'),
        (RouteStatus.COMPLETED.value, 'Completed'),
        (RouteStatus.CANCELLED.value, 'Cancelled')
    ])
    submit = SubmitField('Save Route')


class DocumentSearchForm(FlaskForm):
    """
    Form for searching documents with various filters
    """
    title = StringField('Document Title', validators=[Optional()])
    file_type = SelectField('File Type', validators=[Optional()], choices=[])
    date_from = DateField('From Date', validators=[Optional()], format='%Y-%m-%d')
    date_to = DateField('To Date', validators=[Optional()], format='%Y-%m-%d')
    uploader_id = SelectField('Uploaded By', coerce=int, validators=[Optional()], choices=[])
    company_id = SelectField('Company', coerce=int, validators=[Optional()], choices=[])
    submit = SubmitField('Search')


class OperatorForm(UserForm):
    """
    Form for creating or editing an operator
    """
    manager_id = SelectField('Manager', coerce=int, validators=[Optional()],
                             render_kw={"class": "form-select"})

    def __init__(self, *args, **kwargs):
        super(OperatorForm, self).__init__(*args, **kwargs)
        self.role.data = UserRole.OPERATOR.value

        # Set default choice for manager_id
        if not self.manager_id.choices:
            self.manager_id.choices = [(0, "-- Select Manager --")]