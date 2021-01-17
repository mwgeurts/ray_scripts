""" Load isodose and target visualization
    Loads some simple visual settings for targets and isodoses since this is not a sticky
    property of the RayStation (RS) software

    Version:
    1.0 Load targets as filled. Normalize isodose to prescription, and try to normalize to the
        maximum dose in External or External_Clean

    This program is free software: you can redistribute it and/or modify it under
    the terms of the GNU General Public License as published by the Free Software
    Foundation, either version 3 of the License, or (at your option) any later
    version.
    
    This program is distributed in the hope that it will be useful, but WITHOUT
    ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
    FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
    
    You should have received a copy of the GNU General Public License along with
    this program. If not, see <http://www.gnu.org/licenses/>.
    """

__author__ = 'Adam Bayliss'
__contact__ = 'rabayliss@wisc.edu'
__date__ = '29-Jul-2019'
__version__ = '1.0.0'
__status__ = 'Production'
__deprecated__ = False
__reviewer__ = ''
__reviewed__ = ''
__raystation__ = '8.b.SP2'
__maintainer__ = 'One maintainer'
__email__ = 'rabayliss@wisc.edu'
__license__ = 'GPLv3'
__copyright__ = 'Copyright (C) 2018, University of Wisconsin Board of Regents'
__help__ = 'https://github.com/mwgeurts/ray_scripts/wiki/User-Interface'
__credits__ = []

import connect
import logging
import sys
import UserInterface
import platform
import clr
import StructureOperations as so


def isodose_reconfig(case, ref_dose, max_dose=None, levels=None):
    """
    This function takes the current case, an optional max_dose
    :param case: ScriptObject of RS case
    :param ref_dose: The normalization dose in Gy
    :param max_dose: an optional argument that can specify the maximum dose within the plan
    :param levels: a dictionary of relative isodose levels and colors levels[10]=[R, G, B]
    :return:
    """
    # Clear current contents
    if max_dose:
        max_ratio = max_dose / ref_dose
        max_isodose = int(100 * 0.99 * max_ratio)
    else:
        logging.info('No max dose found. Using 115%')
        max_isodose = int(115)

    dose_levels = {}
    if levels is None:
        dose_levels = {10: so.define_sys_color([127, 0, 255]),
                       20: so.define_sys_color([0, 0, 255]),
                       30: so.define_sys_color([0, 127, 255]),
                       40: so.define_sys_color([0, 255, 255]),
                       50: so.define_sys_color([0, 255, 127]),
                       60: so.define_sys_color([0, 255, 0]),
                       70: so.define_sys_color([127, 255, 0]),
                       80: so.define_sys_color([255, 255, 0]),
                       90: so.define_sys_color([255, 127, 0]),
                       95: so.define_sys_color([255, 0, 0]),
                       100: so.define_sys_color([255, 0, 255]),
                       105: so.define_sys_color([255, 0, 127]),
                       max_isodose: so.define_sys_color([128, 20, 20])}
    else:
        for k, v in levels.items():
            dose_levels[k] = so.define_sys_color(levels[k])

    dose_color_table = {}
    for k, v in dose_levels.items():
        dose_color_table[k] = v

    case.CaseSettings.DoseColorMap.ColorTable = dose_color_table
    case.CaseSettings.DoseColorMap.ReferenceValue = ref_dose
    case.CaseSettings.DoseColorMap.PresentationType = 'Absolute'


def find_max_dose_in_plan(examination, case, plan):
    rois = case.PatientModel.StructureSets[examination.Name].RoiGeometries
    if so.check_structure_exists(case=case,
                                 structure_name='External_Clean',
                                 roi_list=rois,
                                 option='Check'):
        max_dose = plan.TreatmentCourse.TotalDose.GetDoseStatistic(RoiName='External_Clean', DoseType='Max')
    elif so.check_structure_exists(case=case,
                                   structure_name='External',
                                   roi_list=rois,
                                   option='Check'):
        max_dose = plan.TreatmentCourse.TotalDose.GetDoseStatistic(RoiName='External', DoseType='Max')
    else:
        max_dose = None

    return max_dose


def main():
    try:
        patient = connect.get_current('Patient')
        case = connect.get_current('Case')
        examination = connect.get_current("Examination")
        patient_db = connect.get_current('PatientDB')
        plan = connect.get_current('Plan')
        beamset = connect.get_current('BeamSet')
    except Exception as ex:
        template = "An exception of type {0} occurred. Arguments:\n{1!r}"
        message = template.format(type(ex).__name__, ex.args)
        UserInterface.WarningBox(message)
        UserInterface.WarningBox('This script requires a patient, case, and beamset to be loaded')
        sys.exit('This script requires a patient, case, and beamset to be loaded')

    # Capture the current list of ROI's to avoid saving over them in the future
    targets = so.find_targets(case)
    for t in targets:
        patient.Set2DvisualizationForRoi(RoiName=t,
                                         Mode='filled')

    max_dose = find_max_dose_in_plan(examination=examination,
                                     case=case,
                                     plan=plan)

    try:
        ref_dose = beamset.Prescription.PrimaryDosePrescription.DoseValue
    except AttributeError:
        ref_dose = max_dose
        logging.info('Neither prescription nor max dose are defined in the plan. Setting to default max_dose')

    isodose_reconfig(case=case,
                     ref_dose=ref_dose,
                     max_dose=max_dose)


if __name__ == '__main__':
    main()
