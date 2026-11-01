import re

from django import forms
from django.utils.translation import gettext as _

from netbox.models.features import ChangeLoggingMixin
from utilities.forms.fields import QueryField
from utilities.forms.mixins import BackgroundJobMixin, FilterModifierMixin

__all__ = (
    'BulkDeleteForm',
    'BulkEditForm',
    'BulkRenameForm',
    'CSVModelForm',
    'ConfirmationForm',
    'DeleteForm',
    'FilterForm',
    'TableConfigForm',
)


class ConfirmationForm(forms.Form):
    """
    A generic confirmation form. The form is not valid unless the `confirm` field is checked.
    """
    return_url = forms.CharField(
        required=False,
        widget=forms.HiddenInput()
    )
    confirm = forms.BooleanField(
        required=True,
        widget=forms.HiddenInput(),
        initial=True
    )


class DeleteForm(ConfirmationForm):
    """
    Confirm the deletion of an object, optionally providing a changelog message.
    """
    changelog_message = forms.CharField(
        required=False,
        max_length=200
    )

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)

        # Hide the changelog_message filed if the model doesn't support change logging
        if instance is None or not issubclass(instance._meta.model, ChangeLoggingMixin):
            self.fields.pop('changelog_message')


class BulkEditForm(BackgroundJobMixin, forms.Form):
    """
    Provides bulk edit support for objects.

    Attributes:
        nullable_fields: A list of field names indicating which fields support being set to null/empty
    """
    nullable_fields = ()


class BulkRenameForm(forms.Form):
    """
    An extendable form to be used for renaming objects in bulk.
    """
    find = forms.CharField(
        strip=False
    )
    replace = forms.CharField(
        strip=False,
        required=False
    )
    use_regex = forms.BooleanField(
        required=False,
        initial=True,
        label=_('Use regular expressions')
    )

    def clean(self):
        super().clean()

        # Validate regular expression in "find" field
        if self.cleaned_data['use_regex']:
            try:
                re.compile(self.cleaned_data['find'])
            except re.error:
                raise forms.ValidationError({
                    'find': "Invalid regular expression"
                })


class BulkDeleteForm(BackgroundJobMixin, ConfirmationForm):
    pk = forms.ModelMultipleChoiceField(
        queryset=None,
        widget=forms.MultipleHiddenInput
    )
    changelog_message = forms.CharField(
        required=False,
        max_length=200
    )

    def __init__(self, model, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['pk'].queryset = model.objects.all()

        # Hide the changelog_message filed if the model doesn't support change logging
        if model is None or not issubclass(model, ChangeLoggingMixin):
            self.fields.pop('changelog_message')


class CSVModelForm(forms.ModelForm):
    """
    ModelForm used for the import of objects in CSV format.
    """
    id = forms.IntegerField(
        label=_('ID'),
        required=False,
        help_text=_('Numeric ID of an existing object to update (if not creating a new object)')
    )

    def __init__(self, *args, headers=None, **kwargs):
        self.headers = headers or {}
        super().__init__(*args, **kwargs)

        # Translate any human-readable column headers (e.g. those produced by a CSV export, which uses each
        # field's label) back to their corresponding field names. This allows a file exported from the object
        # table to be re-imported without manually renaming its headers. Only headers which do not already match
        # a field name are considered, so existing (field-name) headers are never altered.
        self._translate_header_labels()

        # Modify the model form to accommodate any customized to_field_name properties
        for field, to_field in self.headers.items():
            if to_field is not None and field in self.fields:
                self.fields[field].to_field_name = to_field

    def _build_label_map(self):
        """
        Return a mapping of {normalized field label: field name} for all fields on the form. Labels which map
        to more than one field are omitted to avoid ambiguous translation.
        """
        label_map = {}
        ambiguous = set()
        for name, field in self.fields.items():
            label = field.label
            if not label:
                continue
            key = str(label).strip().lower()
            if key in label_map and label_map[key] != name:
                ambiguous.add(key)
            else:
                label_map[key] = name
        for key in ambiguous:
            label_map.pop(key, None)
        return label_map

    def _translate_header_labels(self):
        """
        Rewrite any CSV header which matches a field's label (rather than its field name) so that it maps to the
        corresponding field name. This mutates both ``self.headers`` and the bound ``self.data`` in place.
        """
        if not self.headers:
            return

        label_map = self._build_label_map()
        renames = {}
        for header in self.headers:
            # Leave headers which already correspond to a real field untouched
            if header in self.fields:
                continue
            translated = label_map.get(str(header).strip().lower())
            # Only translate to a field name not already present as a header, to avoid collisions
            if translated and translated != header and translated not in self.headers:
                renames[header] = translated

        if not renames:
            return

        # Rebuild the headers mapping, preserving the original to_field for each translated header
        self.headers = {
            renames.get(header, header): to_field
            for header, to_field in self.headers.items()
        }

        # Mirror the renames onto the bound data (a QueryDict or dict of {header: value})
        if self.data:
            try:
                data = self.data.copy()
            except AttributeError:
                data = dict(self.data)
            for old_header, new_header in renames.items():
                if old_header in data and new_header not in data:
                    data[new_header] = data.pop(old_header)
            self.data = data

    def clean(self):
        # Flag any invalid CSV headers
        for header in self.headers:
            if header not in self.fields:
                raise forms.ValidationError(
                    _("Unrecognized header: {name}").format(name=header)
                )

        return super().clean()


class FilterForm(FilterModifierMixin, forms.Form):
    """
    Base Form class for FilterSet forms.
    """
    q = QueryField(
        required=False,
        label=_('Search')
    )


class TableConfigForm(forms.Form):
    """
    Form for configuring user's table preferences.
    """
    available_columns = forms.MultipleChoiceField(
        choices=[],
        required=False,
        widget=forms.SelectMultiple(
            attrs={'size': 10, 'class': 'form-select'}
        ),
        label=_('Available Columns')
    )
    columns = forms.MultipleChoiceField(
        choices=[],
        required=False,
        widget=forms.SelectMultiple(
            attrs={'size': 10, 'class': 'form-select select-all'}
        ),
        label=_('Selected Columns')
    )

    def __init__(self, table, *args, **kwargs):
        self.table = table

        super().__init__(*args, **kwargs)

        # Initialize columns field based on table attributes
        if table:
            self.fields['available_columns'].choices = table.available_columns
            self.fields['columns'].choices = table.selected_columns

    @property
    def table_name(self):
        return self.table.__class__.__name__
