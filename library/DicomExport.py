""" DICOM Export Function

    The DicomExport.send() function uses the RayStation ScriptableDicomExport()
    function, pydicom, and pynetdicom3 to export DICOM RT plan data to a temporary
    folder, then modify the contents of the DICOM files, and finally to send the
    modified files to one or more destinations. In this manner, machine names and
    non-standard beam energies (FFF) can be configured in the system.

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
__version__ = '1.0.0'
__license__ = 'GPLv3'
__help__ = 'https://github.com/mwgeurts/ray_scripts/wiki/DICOM-Export'
__copyright__ = 'Copyright (C) 2018, University of Wisconsin Board of Regents'

import xml.etree.ElementTree
import time
import tempfile
import logging
import UserInterface
import os
import pydicom
import pynetdicom3
import shutil
import re

# Parse destination and filters XML files
destinations = xml.etree.ElementTree.parse('DicomDestinations.xml')
filters = xml.etree.ElementTree.parse('DicomFilters.xml')


def send(case, destination, exam=None, beamset=None, beamdose=False, ignore_warnings=False, ignore_errors=False,
         anonymize=None, filter=None, machine=None):
    # Start timer
    tic = time.time()

    # Re-cast string destination as list
    if isinstance(destination, str):
        destination = [destination]

    # Filter machine and energy by default
    if filter is None:
        filter = ['machine', 'energy']

    # Create temporary folders to store original and modified exports
    original = tempfile.mkdtemp()
    modified = tempfile.mkdtemp()

    # Validate destinations
    for d in destination:
        if d not in destinations.keys():
            raise IndexError('The provided DICOM destination is not in the available list')

    # If multiple machine filter options exist, prompt the user to select one
    if machine is None and filter is not None and 'machine' in filter and beamset is not None and filters is not None:
        machine_list = []
        for c in filters.getroot():
            if c.tag == 'filter' and c.attrib['type'] == 'machine/energy':
                match = True
                m = c.findall('from/machine')[0]
                e = float(c.findall('from/energy')[0])
                for b in beamset.Beams:
                    if b.MachineReference.MachineName == m and b.MachineReference.Energy != e:
                        match = False
                        break

                if match:
                    machine_list.append(m)

            elif c.tag == 'filter' and c.attrib['type'] == 'machine' and \
                    c.findall('from/machine')[0] == beamset.MachineReference.MachineName:
                machine_list.append(m)

        if len(machine_list) == 1:
            machine = filters['machine'][beamset.MachineReference.MachineName][0]

        elif len(machine_list) > 1:
            dialog = UserInterface.ButtonList(inputs=machine_list, title='Select a machine to export as')
            machine = dialog.show()

    # Load energy filters for selected machine
    if machine is None and filter is not None and 'machine' in filter and beamset is not None:
        energy_list = {}
        for c in filters.getroot():
            if c.tag == 'filter' and c.attrib['type'] == 'machine/energy' and \
                    c.findall('from/machine')[0] == beamset.MachineReference.MachineName:
                for t in c.findall('to'):
                    if t.findall('machine')[0] == machine:
                        energy_list[float(c.findall('from/energy')[0])] = t.findall('energy')[0]

    # Establish connections with all SCP destinations
    bar = UserInterface.ProgressBar(text='Establishing connection to DICOM destinations',
                                    title='Export Progress',
                                    marquee=True)
    for d in destination:
        if len({'host', 'aet', 'port'}.difference(destinations[d])) == 0:
            ae = pynetdicom3.AE(scu_sop_class=['1.2.840.10008.1.1'])
            logging.debug('Requesting Association with {}'.format(destinations[d]['host']))
            assoc = ae.associate(destinations[d]['host'], destinations[d]['port'])
            if assoc.is_established:
                logging.debug('Association accepted by the peer')
                status = assoc.send_c_echo()
                assoc.release()
                logging.debug('C-ECHO Response: 0x{0:04x}'.format(status.Status))

            elif assoc.is_rejected:
                bar.close()
                raise IOError('Association to {} was rejected by the peer'.format(destinations[d]['host']))

            elif assoc.is_aborted:
                bar.close()
                raise IOError('Received A-ABORT from the peer during association to {}'.
                              format(destinations[d]['host']))

    # Export data to original folder
    args = {'IgnorePreConditionWarnings': ignore_warnings, 'DicomFilter': '', 'ExportFolderPath': original}

    if exam is not None:
        args['Examinations'] = [exam.Name]

    if beamset is not None:
        args['RtStructureSetsReferencedFromBeamSets'] = [beamset.BeamSetIdentifier()]
        args['BeamSets'] = [beamset.BeamSetIdentifier()]

        if beamdose:
            args['BeamDosesForBeamSets'] = [beamset.BeamSetIdentifier()]

    if anonymize is not None and hasattr(anonymize, 'name') and hasattr(anonymize, 'id'):
        args['Anonymize'] = True
        args['AnonymizedName'] = anonymize['name']
        args['AnonymizedId'] = anonymize['id']

    bar.update(text='Exporting DICOM files to temporary folder')
    try:
        logging.debug('Executing ScriptableDicomExport() to path {}'.format(original))
        case.ScriptableDicomExport(**args)

    except Exception as error:
        if ignore_errors:
            logging.warning(str(error))

        else:
            bar.close()
            raise

    # Load the DICOM files back in, applying filters
    edited = {}
    bar.update(text='Applying filters')
    for o in os.listdir(original):

        # Try to open as a DICOM file
        try:
            logging.debug('Reading original file {}'.format(o))
            ds = pydicom.dcmread(os.path.join(original, o))

            # If this is a DICOM RT plan
            edits = []
            if ds.file_meta.MediaStorageSOPClassUID == '1.2.840.10008.5.1.4.1.1.481.5':

                # If applying a machine filter
                for b in ds.BeamSequence:
                    if machine is not None:

                        if b.TreatmentMachineName != machine:
                            logging.debug('Updating {} on beam {} to {}'.format(
                                str(b.data_element('TreatmentMachineName').tag), b.BeamNumber, machine))
                            b.TreatmentMachineName = machine
                            edits.append(str(b.data_element('TreatmentMachineName').tag))

                    # If applying an energy filter
                    if filter is not None and 'energy' in filter and hasattr(b, 'ControlPointSequence'):
                        for c in b.ControlPointSequence:
                            if hasattr(c, 'NominalBeamEnergy') and c.NominalBeamEnergy in energy_list.keys():
                                e = float(re.sub('\D+', '', energy_list[c.NominalBeamEnergy]))
                                m = re.sub('\d+', '', energy_list[c.NominalBeamEnergy])
                                if c.NominalBeamEnergy != e:
                                    logging.debug('Updating {} on beam {}, CP {} to {}'.format(str(
                                        c.data_element('NominalBeamEnergy').tag), b.BeamNumber, c.ControlPointIndex, e))
                                    c.NominalBeamEnergy = e
                                    edits.append(str(c.data_element('NominalBeamEnergy').tag))

                                if not hasattr(b, 'FluenceModeID') or b.FluenceModeID != m:
                                    logging.debug('Updating {} on beam {}, CP {} to {}'.format(
                                        str(b.data_element('FluenceModeID').tag), b.BeamNumber, c.ControlPointIndex, m))

                                    b.FluenceModeID = m
                                    edits.append(str(b.data_element('FluenceModeID').tag))
                                    if m != '':
                                        logging.debug('Updating {} on beam {}, CP {} to {}'.format(
                                            str(b.data_element('FluenceMode').tag), b.BeamNumber, c.ControlPointIndex,
                                            'NON_STANDARD'))

                                        b.FluenceMode = 'NON_STANDARD'
                                        edits.append(str(b.data_element('FluenceMode').tag))

            # If no edits are needed, copy the file to the modified directory
            if len(edits) == 0:
                logging.debug('File {} does not require modification, and will be copied directly'.format(o))
                shutil.copy(os.path.join(original, o), modified)

            else:
                edited[o] = edits
                logging.debug('File {} re-saved with {} edits'.format(o, len(edits)))
                ds.save_as(os.path.join(modified, o))

        except pydicom.errors.InvalidDicomError:
            if ignore_errors:
                logging.warning('File {} could not be read during modification, skipping'.format(o))

            else:
                bar.close()
                raise

    # Send each file
    i = 0
    total = len(os.listdir(modified))
    for m in os.listdir(modified):
        i += 1
        bar.update(text='Validating and Exporting Files ({} of {})'.format(i, total))
        try:
            logging.debug('Reading modified file {}'.format(m))
            ds = pydicom.dcmread(os.path.join(modified, m))

            # Validate changes against original file
            if m in edited:
                logging.debug('Validating edits')
                dso = pydicom.dcmread(os.path.join(original, m))
                edits = []
                for k0 in ds.keys():
                    if ds[k0].VR == 'SQ':
                        for i0 in range(len(ds[k0].value)):
                            print '1 Checking {} index {}'.format(str(ds[k0].tag), i0)
                            for k1 in ds[k0].value[i0].keys():
                                if ds[k0].value[i0][k1].VR == 'SQ':
                                    for i1 in range(len(ds[k0].value[i0][k1].value)):
                                        print '2 Checking {} index {}'.format(str(ds[k0].value[i0][k1].tag), i1)
                                        for k2 in ds[k0].value[i0][k1].value[i1].keys():
                                            if ds[k0].value[i0][k1].value[i1][k2].VR == 'SQ':
                                                for i2 in range(len(ds[k0].value[i0][k1].value[i1][k2].value)):
                                                    print '3 Checking {} index {}'.format(str(ds[k0].value[i0][k1].value[i1][k2].tag), i2)
                                                    for k3 in ds[k0].value[i0][k1].value[i1][k2].value[i2].keys():
                                                        if ds[k0].value[i0][k1].value[i1][k2].value[i2][k3].VR == 'SQ':
                                                            raise KeyError('Too many nested sequences')

                                                        elif k3 not in dso[k0].value[i0][k1]. \
                                                                value[i1][k2].value[i2]:
                                                            edits.append(str(ds[k0].value[i0][k1].value[i1][k2].
                                                                             value[i2][k3].tag))

                                                        elif ds[k0].value[i0][k1].value[i1][k2].value[i2][k3].value != \
                                                                dso[k0].value[i0][k1].value[i1][k2]. \
                                                                        value[i2][k3].value:
                                                            edits.append(str(ds[k0].value[i0][k1].value[i1][k2].
                                                                             value[i2][k3].tag))

                                            elif k2 not in dso[k0].value[i0][k1].value[i1]:
                                                edits.append(str(ds[k0].value[i0][k1].value[i1][k2].tag))

                                            elif ds[k0].value[i0][k1].value[i1][k2].value != \
                                                    dso[k0].value[i0][k1].value[i1][k2].value:
                                                edits.append(str(ds[k0].value[i0][k1].value[i1][k2].tag))

                                elif k1 not in dso[k0].value[i0]:
                                    edits.append(str(ds[k0].value[i0][k1].tag))

                                elif ds[k0].value[i0][k1].value != dso[k0].value[i0][k1].value:
                                    edits.append(str(ds[k0].value[i0][k1].tag))

                    elif k0 not in dso:
                        edits.append(str(ds[k0].tag))

                    elif ds[k0].value != dso[k0].value:
                        edits.append(str(ds[k0].tag))

                if len(edits) == len(edited[m]) and edits.sort() == edited[m].sort():
                    logging.debug('File {} edits are consistent with expected'.format(m))

                else:
                    bar.close()
                    logging.error('Expected modification tags: ' + ', '.join(edited[m]))
                    logging.error('Observed modification tags: ' + ', '.join(edits))
                    raise KeyError('DICOM Export modification inconsistency detected')

            for d in destination:
                if len({'host', 'aet', 'port'}.difference(destinations[d])) == 0:
                    ae = pynetdicom3.AE(scu_sop_class=pynetdicom3.StorageSOPClassList)
                    assoc = ae.associate(destinations[d]['host'], destinations[d]['port'])
                    if assoc.is_established:
                        status = assoc.send_c_store(dataset=ds,
                                                    msg_id=1,
                                                    priority=0,
                                                    originator_aet='RayStation',
                                                    originator_id=None)
                        assoc.release()
                        logging.info('{0} -> {1} C-STORE status: 0x{2:04x}'.format(m, d, status.Status))
                        if status.Status != 0:
                            raise IOError('C-STORE ERROR: 0x{2:04x}'.format(m, d, status.Status))

                    elif assoc.is_rejected:
                        bar.close()
                        raise IOError('Association to {} was rejected by the peer'.format(destinations[d]['host']))

                    elif assoc.is_aborted:
                        bar.close()
                        raise IOError('Received A-ABORT from the peer during association to {}'.
                                      format(destinations[d]['host']))

                elif 'path' in destinations[d]:
                    logging.info('Exporting {} to {}'.format(m, destinations[d]['path']))
                    shutil.copy(os.path.join(modified, m), destinations[d]['path'])

        except pydicom.errors.InvalidDicomError:
            if ignore_errors:
                logging.warning('File {} could not be read during modification, skipping'.format(m))

            else:
                bar.close()
                raise

    # Delete temporary folders
    logging.debug('Deleting temporary folder {}'.format(original))
    shutil.rmtree(original)
    logging.debug('Deleting temporary folder {}'.format(modified))
    shutil.rmtree(modified)

    # Log completion
    logging.debug('Export completed successfully in {:.3f} seconds'.format(time.time() - tic))
    bar.close()
    UserInterface.MessageBox('DICOM Export Successful', 'Export Success')


from connect import *
import sys

root = logging.getLogger()
root.setLevel(logging.DEBUG)

ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(levelname)s\t%(message)s')
ch.setFormatter(formatter)
root.addHandler(ch)

send(case=get_current('Case'),
     destination='Transfer Folder',
     exam=get_current('Examination'),
     beamset=get_current('BeamSet'),
     filter=['machine', 'energy'],
     ignore_warnings=True)
