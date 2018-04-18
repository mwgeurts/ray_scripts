""" Import and Re-calculate DICOM RT Plans
    
    This script imports all DICOM data from a selected directory into the current
    patient, updates the dose grid, re-calculates, and finally exports the plan and
    beam doses for each plan to a destination directory.

    This script is useful when you have a large group of patient specific RT Plans
    (created from QA Preparation, for example) that you would like to quickly
    re-compute and re-export for analysis. If the export folder browser is cancelled,
    the script will import and re-calculate but not export the files.

    This script will re-write each DICOM RT plan to a temporary directory, first to
    re-identify the file to the selected patient, then to update the machine and/or
    beam isocenter depending on the import overrides.
    
    This program is free software: you can redistribute it and/or modify it under
    the terms of the GNU General Public License as published by the Free Software
    Foundation, either version 3 of the License, or (at your option) any later version.
    
    This program is distributed in the hope that it will be useful, but WITHOUT
    ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
    FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
    
    You should have received a copy of the GNU General Public License along with
    this program. If not, see <http://www.gnu.org/licenses/>.
    """

__author__ = 'Mark Geurts'
__contact__ = 'mark.w.geurts@gmail.com'
__version__ = '1.1.0'
__license__ = 'GPLv3'
__help__ = 'https://github.com/mwgeurts/ray_scripts/wiki/Import-and-Recalc-Plan-Dose'
__copyright__ = 'Copyright (C) 2018, University of Wisconsin Board of Regents'

# Import packages
import sys
import os
import connect
import UserInterface
import logging
import pydicom
import tempfile
import shutil
import time


def main():

    # Get current patient, case, and machine DB
    machine_db = connect.get_current('MachineDB')
    try:
        patient = connect.get_current('Patient')
        case = connect.get_current('Case')

    except Exception:
        UserInterface.WarningBox('This script requires a patient to be loaded')
        sys.exit('This script requires a patient to be loaded')

    # Start script status
    status = UserInterface.ScriptStatus(steps=['Verify CT density table and external are set',
                                               'Select folder to import DICOM RT plans from',
                                               'Select folder to export calculated dose to',
                                               'Choose import overrides and calculation options',
                                               'Import, re-calculate, and export plans'],
                                        docstring=__doc__,
                                        help=__help__)

    # Confirm a CT density table and external contour was set
    status.next_step(text='Prior to execution, the script will make sure that a CT density table and External ' +
                          'contour are set for the current plan. These are required for dose calculation.')
    examination = connect.get_current('Examination')
    if examination.EquipmentInfo.ImagingSystemReference is None:
        connect.await_user_input('The CT imaging system is not set. Set it now, then continue the script.')
        patient.Save()

    else:
        patient.Save()

    external = False
    for r in case.PatientModel.RegionsOfInterest:
        if r.Type == 'External':
            external = True

    if not external:
        connect.await_user_input('No external contour was found. Generate an external contour, then continue ' +
                                 'the script.')
        patient.Save()

    # Define path to search for RT PLANS
    status.next_step(text='For this step, select a folder containing one or more DICOM RT Plans to import, then ' +
                          'click OK. the plans may be from the same patient, or different patients.')
    common = UserInterface.CommonDialog()
    import_path = common.folder_browser('Select the path containing DICOM RT Plans to import:')
    if import_path == '':
        logging.info('Folder not selected, import will be skipped')

    else:
        logging.info('Import folder set to {}'.format(import_path))

    # Define path to export RT PLAN/DOSE to
    status.next_step(text='Next, select a folder to export the resulting DICOM RT dose volumes to, then click OK. ' +
                          'You can also skip export by clicking Cancel.')
    export_path = common.folder_browser('Select the path to export dose to (cancel to skip export):')
    beams = False
    if export_path == '':
        logging.info('Folder not selected, export will be skipped')

    else:
        logging.info('Export folder set to {}'.format(export_path))
        beams = UserInterface.QuestionBox('Export beam doses? Beamset doses are always exported', 'Export Beam Dose')

        if beams.no:
            logging.info('Beam dose export disabled')

    # Ask the user to select a machine to re-compute on
    status.next_step(text='Next, select a machine model to compute on. If left blank, the existing machine in the ' +
                          'DICOM RT Plan will be used. You can also choose whether or not to re-center each plan to ' +
                          'the CT origin (0,0,0). Note, if the selected machine is not compatible with a plan (due ' +
                          'to energy or MLC differences), the import will occur but calculation and export will be ' +
                          'skipped.')
    machines = machine_db.QueryCommissionedMachineInfo(Filter={})
    machine_list = []
    for i, m in enumerate(machines):
        if m['IsCommissioned']:
            machine_list.append(m['Name'])

    dialog = UserInterface.InputDialog(title='Select Import Overrides',
                                       inputs={'a': 'Select a machine model to re-calculate plans (leave blank to ' +
                                                    'use existing machine):',
                                               'b': 'Choose whether to re-center isocenter to (0,0,0):',
                                               'c': 'Enter the calculation resolution (mm):'},
                                       datatype={'a': 'combo', 'b': 'combo', 'c': 'text'},
                                       options={'a': machine_list, 'b': ['Yes', 'No']},
                                       initial={'b': 'No', 'c': '2'},
                                       required=['b', 'c'])
    inputs = dialog.show()
    if 'a' in inputs:
        machine = inputs['a']

    else:
        machine = ''

    res = float(inputs['c'])/10
    if inputs['b'] == 'Yes':
        center = True

    else:
        center = False

    # Walk through import folder, looking for DICOM RT plans
    tic = time.time()
    status.next_step(text='The script is searching for DICOM RT plans. With each plan, the script will import, ' +
                          're-calculate, and export the resulting dose volumes (if selected).')
    patient.Save()
    for s, d, files in os.walk(import_path):
        for f in files:

            # Try to open as a DICOM file
            try:
                logging.debug('Reading file {}'.format(os.path.join(s, f)))
                ds = pydicom.dcmread(os.path.join(import_path, s, f))

                # If this is a DICOM RT plan
                if ds.file_meta.MediaStorageSOPClassUID == '1.2.840.10008.5.1.4.1.1.481.5':

                    # Update DICOM RT plan
                    ds.PatientName = pydicom.valuerep.PersonName(patient.Name)
                    ds.PatientID = patient.PatientID
                    ds.FrameOfReferenceUID = examination.EquipmentInfo.FrameOfReference
                    for b in ds.BeamSequence:
                        if machine != '':
                            b.TreatmentMachineName = machine

                        if center:
                            for c in b.ControlPointSequence:
                                if hasattr(c, 'IsocenterPosition'):
                                    c.IsocenterPosition = ['0', '0', '0']

                    # Save to temp folder and import
                    temp = tempfile.mkdtemp()
                    ds.save_as(os.path.join(temp, f))
                    try:
                        patient.ImportDicomDataFromPath(CaseName=case.CaseName,
                                                        Path=temp,
                                                        SeriesFilter={},
                                                        ImportFilters=[])
                        patient.Save()

                    except Exception as e:
                        logging.warning(str(e))

                    shutil.rmtree(temp)

                    # Update dose grid and re-calculate
                    plans = case.QueryPlanInfo(Filter={'Name': ds.RTPlanLabel})
                    plan = case.LoadPlan(PlanInfo=plans[len(plans) - 1])
                    plan.SetDefaultDoseGrid(VoxelSize={'x': res, 'y': res, 'z': res})
                    patient.Save()

                    beamset = plan.BeamSets[0]
                    beamset.SetCurrent()
                    try:
                        beamset.ComputeDose(ComputeBeamDoses=True, DoseAlgorithm='CCDose')
                        patient.Save()

                    except Exception as e:
                        logging.warning(str(e))

                    # Export plan
                    if export_path != '':
                        try:
                            if beams.yes:
                                case.ScriptableDicomExport(ExportFolderPath=export_path,
                                                           BeamSets=[beamset.BeamSetIdentifier()],
                                                           BeamSetDoseForBeamSets=[beamset.BeamSetIdentifier()],
                                                           BeamDosesForBeamSets=[beamset.BeamSetIdentifier()],
                                                           DicomFilter='',
                                                           IgnorePreConditionWarnings=True)
                            else:
                                case.ScriptableDicomExport(ExportFolderPath=export_path,
                                                           BeamSets=[beamset.BeamSetIdentifier()],
                                                           BeamSetDoseForBeamSets=[beamset.BeamSetIdentifier()],
                                                           DicomFilter='',
                                                           IgnorePreConditionWarnings=True)

                        except Exception as e:
                            logging.warning(str(e))
                else:
                    logging.debug('Non-RT plan class UID identified: {}'.format(ds.file_meta.MediaStorageSOPClassUID))

            except pydicom.errors.InvalidDicomError:
                logging.debug('File {} could not be read as a DICOM file, skipping'.
                              format(os.path.join(import_path, s, f)))

    # Finish up
    logging.debug('ImportRecalcDICOMPlans finished successfully in {:.3f} seconds'.format(time.time() - tic))
    status.finish(text='Script execution successful.')


if __name__ == '__main__':
    main()
