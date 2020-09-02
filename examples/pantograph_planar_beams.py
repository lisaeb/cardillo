from pickle import load
from cardillo.solver.solution import load_solution, save_solution
import numpy as np
from math import pi, ceil, sin, cos, exp
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from numpy.core.function_base import linspace
from numpy.lib.function_base import disp
import meshio
import os

from cardillo.model import Model
from cardillo.model.classical_beams.planar import Euler_bernoulli, Hooke, Inextensible_Euler_bernoulli
from cardillo.model.bilateral_constraints.implicit import Spherical_joint2D, Rigid_connection2D, Revolute_joint2D
from cardillo.model.scalar_force_interactions.force_laws import Linear_spring
from cardillo.model.scalar_force_interactions import add_rotational_forcelaw
from cardillo.solver.newton import Newton
from cardillo.solver.euler_backward import Euler_backward
from cardillo.solver import Generalized_alpha_1, Scipy_ivp
from cardillo.discretization.B_spline import uniform_knot_vector
from cardillo.model.frame import Frame
from cardillo.math.algebra import A_IK_basic_z

def post_processing(subsystem, t, q, filename, u=None, binary=True, dim=3):
    # write paraview PVD file collecting time and all vtk files, see https://www.paraview.org/Wiki/ParaView/Data_formats#PVD_File_Format
    from xml.dom import minidom
    
    root = minidom.Document()
    
    vkt_file = root.createElement('VTKFile')
    vkt_file.setAttribute('type', 'Collection')
    root.appendChild(vkt_file)
    
    collection = root.createElement('Collection')
    vkt_file.appendChild(collection)

    if u is None:
        u = np.zeros_like(q)

    for i, (ti, qi, ui) in enumerate(zip(t, q, u)):
        filei = filename + f'{i}.vtu'

        # write time step and file name in pvd file
        dataset = root.createElement('DataSet')
        dataset.setAttribute('timestep', f'{ti:0.6f}')
        dataset.setAttribute('file', filei)
        collection.appendChild(dataset)

        geom_points = np.array([]).reshape(0, dim)
        cells = []
        HigherOrderDegrees = []
        point_data = {}
        offset = 0

        for subsystemi in subsystem:
            geom_pointsi, point_datai, cellsi, HigherOrderDegreesi = subsystemi.post_processing_subsystem(ti, qi[subsystemi.qDOF], ui[subsystemi.uDOF], binary=binary)

            geom_points = np.append(geom_points, geom_pointsi, axis=0)

            # update cell type and global connectivity
            for k, (cell_type, connectivity) in enumerate(cellsi):
                cellsi[k] = (cell_type, connectivity + offset)
            cells.extend(cellsi)
            offset = cellsi[-1][-1][-1,-1] + 1

            HigherOrderDegrees.extend(HigherOrderDegreesi)

            # update point_data dictionary. For first subsystem generate dictionary
            for key in point_datai:
                if key in point_data:
                    point_data.update({key: np.append(point_data[key], point_datai[key], axis=0)})
                else:
                    point_data.update({key: point_datai[key]})
            

        # write vtk mesh using meshio
        meshio.write_points_cells(
            os.path.splitext(os.path.basename(filei))[0] + '.vtu',
            geom_points, # only export centerline as geometry here!
            cells,
            point_data=point_data,
            cell_data={"HigherOrderDegrees": HigherOrderDegrees},
            binary=binary
        )

    # write pvd file        
    xml_str = root.toprettyxml(indent ="\t")          
    with open(filename + '.pvd', "w") as f:
        f.write(xml_str)

if __name__ == "__main__":
    statics = True
    solveProblem = False
    
    t1 = 5e-2
    dt = t1 / 1500
    # physical parameters
    gamma = pi/4
    # nRow = 2
    # nCol = 100
    nRow = 20
    nCol = 400
    nf = nRow / 2

    H = 0.07
    L = nCol / nRow * H
    LBeam = H / (nRow * sin(gamma))

    Yb = 500e6
    Gb = Yb / (2 * (1 + 0.4))
    a = 1.6e-3
    b = 1e-3
    rp = 0.45e-3
    hp = 1e-3

    Jg = (a * b**3) / 12
    
    EA = Yb * a * b
    EI = Yb * Jg
    GI = Gb * 0.5*(np.pi * rp**4)/hp

    displ = H / 5
    # EA = 1.6e9 * 1.6e-3 * 0.9e-3
    # EI = 1.6e9 * (1.6e-3) * (0.9e-3)**3 / 12
    # GI = 0.1 * 1/3 * 1.6e9 * np.pi * ((0.9e-3)**4)/32 * 1e3 

    # EA = 1.34e5
    # EI = 1.92e-2
    # GI = 1.59e2 * LBeam**2

    # EA = 2304
    # EI = 1.555e-4
    # GI = 0.004

    displacementX_l = 0#displ #-0.0567/4
    # displacementX = 0.02
    displacementY_l = 0.0
    rotationZ_l = 0 #-np.pi/10

    displacementX_r = 0.0567/5
    # displacementX = 0.02
    displacementY_r = 0.00
    
    rotationZ_r = 0 #np.pi/10

    # r_OP_l = lambda t: np.array([0, H / 2, 0]) + np.array([t * displacementX_l, t * displacementY_r, 0])

    fcn = lambda t: displ * np.exp(-(t-0.004)**2/0.001**2)*(t*(t<0.001)+0.001*(t>=0.001))/0.001

    # fig, ax = plt.subplots()
    # ax.set_xlabel('x [m]')
    # ax.set_ylabel('y [m]')
    # x = linspace(0, t1, 1000)
    # y = []

    # for t in x:
    #     y.append(fcn(t))

    # ax.plot(x, y)
    # plt.show()

    r_OP_l = lambda t: np.array([0, H / 2, 0]) + np.array([fcn(t), 0, 0])

    A_IK_l = lambda t: A_IK_basic_z(t * rotationZ_l)

    r_OP_r = lambda t: np.array([L, H / 2, 0]) +  np.array([t * displacementX_r, t * displacementY_r, 0])
    A_IK_r = lambda t: A_IK_basic_z(t * rotationZ_r)
    
    A_rho0 = 930 * a * b
    material_model = Hooke(EA, EI)

    ###################
    # create pantograph
    ###################
    p = 2
    assert p >= 2
    # nQP = int(np.ceil((p + 1)**2 / 2))
    nQP = p + 2

    print(f'nQP: {nQP}')
    nEl = 1

    # projections of beam length
    Lx = LBeam * cos(gamma)
    Ly = LBeam * sin(gamma)

    # upper left node
    xUL = 0         
    yUL = Ly*nRow

    # build reference configuration
    nNd = nEl + p
    X0 = np.linspace(0, LBeam, nNd)
    Xi = uniform_knot_vector(p, nEl)
    for i in range(nNd):
        X0[i] = np.sum(Xi[i+1:i+p+1])
    Y1 = -np.copy(X0) * Ly / p
    X1 = X0 * Lx / p

    X2 = np.copy(X1)
    Y2 = -np.copy(Y1)
    
    # create model
    model = Model()

    # create beams
    beams = []
    ID_mat = np.zeros((nRow, nCol)).astype(int)
    ID = 0
    for brow in range(0, nRow, 2):
        for bcol in range(0, nCol, 2):
            X = X1 + xUL + Lx * bcol
            Y = Y1 + yUL - Ly * brow

            # beam 1
            Q = np.concatenate([X, Y])
            q0 = np.copy(Q)
            u0 = np.zeros_like(Q)
            # beams.append(Euler_bernoulli(A_rho0, material_model, p, nEl, nQP, Q, q0=Q, u0=u0))
            beams.append(Inextensible_Euler_bernoulli(A_rho0, material_model, p, nEl, nQP, Q, q0=Q, u0=u0))
            model.add(beams[ID])
            ID_mat[brow, bcol] = ID
            ID = ID + 1
            
            # beam 2
            Q = np.concatenate([X2 + X[-1], Y2 + Y[-1]])
            q0 = np.copy(Q)
            u0 = np.zeros_like(Q)
            # beams.append(Euler_bernoulli(A_rho0, material_model, p, nEl, nQP, Q, q0=Q, u0=u0))
            beams.append(Inextensible_Euler_bernoulli(A_rho0, material_model, p, nEl, nQP, Q, q0=Q, u0=u0))
            model.add(beams[ID])
            ID_mat[brow, bcol + 1] = ID
            ID = ID + 1

            
    for brow in range(1, nRow, 2):
        for bcol in range(0, nCol, 2):
            X = X2 + xUL + Lx * bcol
            Y = Y2 + yUL - Ly * (brow + 1)
            # beam 1
            Q = np.concatenate([X, Y])
            q0 = np.copy(Q)
            u0 = np.zeros_like(Q)
            # beams.append(Euler_bernoulli(A_rho0, material_model, p, nEl, nQP, Q, q0=Q, u0=u0))
            beams.append(Inextensible_Euler_bernoulli(A_rho0, material_model, p, nEl, nQP, Q, q0=Q, u0=u0))
            model.add(beams[ID])
            ID_mat[brow, bcol] = ID
            ID = ID + 1
            # beam 2
            Q = np.concatenate([X1 + X[-1], Y1 + Y[-1]])
            q0 = np.copy(Q)
            u0 = np.zeros_like(Q)
            # beams.append(Euler_bernoulli(A_rho0, material_model, p, nEl, nQP, Q, q0=Q, u0=u0))
            beams.append(Inextensible_Euler_bernoulli(A_rho0, material_model, p, nEl, nQP, Q, q0=Q, u0=u0))
            model.add(beams[ID])
            ID_mat[brow, bcol + 1] = ID
            ID = ID + 1

    # junctions in the beam families 

    frame_ID1 = (1,)
    frame_ID2 = (0,)
            
    # odd colums
    for bcol in range(0, nCol, 2):
        for brow in range(0, nRow, 2):
            beam1 = beams[ID_mat[brow, bcol]]
            beam2 = beams[ID_mat[brow + 1, bcol + 1]]
            r_OB = beam1.r_OP(0, beam1.q0[beam1.qDOF_P(frame_ID1)], frame_ID1)
            model.add(Rigid_connection2D(beam1, beam2, r_OB, frame_ID1=frame_ID1, frame_ID2=frame_ID2))

            beam1 = beams[ID_mat[brow + 1, bcol]]
            beam2 = beams[ID_mat[brow, bcol + 1]]
            r_OB = beam1.r_OP(0, beam1.q0[beam1.qDOF_P(frame_ID1)], frame_ID1)
            model.add(Rigid_connection2D(beam1, beam2, r_OB, frame_ID1=frame_ID1, frame_ID2=frame_ID2))

    # even columns
    for bcol in range(1, nCol - 1, 2):
        for brow in range(1, nRow - 1, 2):
            beam1 = beams[ID_mat[brow, bcol]]
            beam2 = beams[ID_mat[brow + 1, bcol + 1]]
            r_OB = beam1.r_OP(0, beam1.q0[beam1.qDOF_P(frame_ID1)], frame_ID1)
            model.add(Rigid_connection2D(beam1, beam2, r_OB, frame_ID1=frame_ID1, frame_ID2=frame_ID2))

            beam1 = beams[ID_mat[brow + 1, bcol]]
            beam2 = beams[ID_mat[brow, bcol + 1]]
            r_OB = beam1.r_OP(0, beam1.q0[beam1.qDOF_P(frame_ID1)], frame_ID1)
            model.add(Rigid_connection2D(beam1, beam2, r_OB, frame_ID1=frame_ID1, frame_ID2=frame_ID2))

    # pivots and torsional springs between beam families
            
    # internal pivots
    for brow in range(0, nRow, 2):
        for bcol in range(0, nCol - 1):
            beam1 = beams[ID_mat[brow, bcol]]
            beam2 = beams[ID_mat[brow, bcol + 1]]
            r_OB = beam1.r_OP(0, beam1.q0[beam1.qDOF_P(frame_ID1)], frame_ID1)
            # model.add(Revolute_joint2D(beam1, beam2, r_OB, np.eye(3), frame_ID1=frame_ID1, frame_ID2=frame_ID2))
            spring = Linear_spring(GI)
            model.add(add_rotational_forcelaw(spring, Revolute_joint2D)(beam1, beam2, r_OB, np.eye(3), frame_ID1=frame_ID1, frame_ID2=frame_ID2))

    # lower boundary pivots
    for bcol in range(1, nCol - 1, 2):
        beam1 = beams[ID_mat[-1, bcol]]
        beam2 = beams[ID_mat[-1, bcol + 1]]
        r_OB = beam1.r_OP(0, beam1.q0[beam1.qDOF_P(frame_ID1)], frame_ID1)
        # model.add(Revolute_joint2D(beam1, beam2, r_OB, np.eye(3), frame_ID1=frame_ID1, frame_ID2=frame_ID2))
        spring = Linear_spring(GI)
        model.add(add_rotational_forcelaw(spring, Revolute_joint2D)(beam1, beam2, r_OB, np.eye(3), frame_ID1=frame_ID1, frame_ID2=frame_ID2))

    # clamping at the left hand side
    frame_l = Frame(r_OP=r_OP_l, A_IK=A_IK_l)
    model.add(frame_l)
    for idx in ID_mat[:, 0]:
        beam = beams[idx]
        r_OB = beam.r_OP(0, beam.q0[beam.qDOF_P(frame_ID2)], frame_ID=frame_ID2)
        model.add(Rigid_connection2D(frame_l, beam, r_OB, frame_ID2=frame_ID2))

    # clamping at the right hand side
    frame_r = Frame(r_OP=r_OP_r, A_IK = A_IK_r)
    model.add(frame_r)
    for idx in ID_mat[:, -1]:
        beam = beams[idx]
        r_OB = beam.r_OP(0, beam.q0[beam.qDOF_P(frame_ID1)], frame_ID=frame_ID1)
        model.add(Rigid_connection2D(beam, frame_r, r_OB, frame_ID1=frame_ID1))

    # assemble model
    model.assemble()

    # print(f'ID matrix:{ID_mat}')

    # plot initial configuration
    fig, ax = plt.subplots()
    ax.set_xlabel('x [m]')
    ax.set_ylabel('y [m]')
    ax.set_xlim([-Ly, Ly*(nCol+1)])
    ax.set_ylim([-Ly, Ly*(nRow+1)])
    ax.grid(linestyle='-', linewidth='0.5')
    ax.set_aspect('equal')

    # n_plt = 5
    # bdy = beams[0]
    # q_body = model.q0[bdy.qDOF]
    # xi_plt = np.linspace(0, 1, n_plt)
    # NN = np.zeros((len(xi_plt), 2, bdy.nq_el))
    # bdy_qDOF_P = np.zeros((len(xi_plt), bdy.nq_el), dtype=int)

    # for i, xi in enumerate(xi_plt):
    #     frame_ID = (xi,)
    #     bdy_qDOF_P[i] = bdy.qDOF_P(frame_ID)
    #     if xi == 0:
    #         NN[i] = bdy.N_bdry[0]
    #     elif xi == 1:
    #         NN[i] = bdy.N_bdry[1]
    #     else:
    #         N = B_spline_basis(bdy.polynomial_degree, 0, bdy.knot_vector, xi)
    #         NN[i] = bdy.stack_shapefunctions(N)

    # for bdy in beams:
    #     q_body = model.q0[bdy.qDOF]
    #     r = []
    #     for i, xi in enumerate(xi_plt):
    #         qp = q_body[bdy_qDOF_P[i]]
    #         r.append(NN[i] @ qp)

    #     x, y = np.array(r).T
    #     ax.plot(x, y, '--k')

    # plt.show()

    # for bdy in beams:
    #     x, y, z = bdy.centerline(model.q0, n=2).T
    #     ax.plot(x, y, '--k')

    # plt.show()

    ######################
    # solve static problem
    ######################
    if statics:
        solver = Newton(model, n_load_steps=3, max_iter=50, tol=1.0e-10, numerical_jacobian=False)
    else:
        # solver = Euler_backward(model, t1, dt, newton_max_iter=50, numerical_jacobian=False, debug=False)
        solver = Generalized_alpha_1(model, t1, dt, variable_dt=False, rho_inf=0.8)
        # solver = Scipy_ivp(model, t1, dt, atol=1e-6)

    if solveProblem == True:
        sol = solver.solve()
        save_solution(sol, 'pantograph20times400')
    else:
        sol = load_solution('pantograph20times400-2')

    

    # exit()

    post_processing(beams, sol.t[::5], sol.q[::5], 'PantographDynamicLonger', u=sol.u[::5], dim=2, binary=True)

    # if statics:
    #     fig, ax = plt.subplots()
    #     ax.set_xlabel('x [m]')
    #     ax.set_ylabel('y [m]')
    #     # ax.set_xlim([-Ly + H/2 * sin(rotationZ_l), Ly*(nCol+1) + displacementX + H/2 * sin(rotationZ_r)])
    #     # ax.set_ylim([-Ly, Ly*(nRow+1) + displacementY])
    #     ax.grid(linestyle='-', linewidth='0.5')
    #     ax.set_aspect('equal')

    #     for bdy in beams:
    #         x, y, z = bdy.centerline(sol.q[-1]).T
    #         ax.plot(x, y, '-b')

    #     plt.show()
    # else:
    #     # animate configurations
    #     fig, ax = plt.subplots()
    #     ax.set_xlabel('x [m]')
    #     ax.set_ylabel('y [m]')
    #     ax.set_xlim([-Ly + H/2 * sin(rotationZ_l), Ly*(nCol+1) + displacementX_r + H/2 * sin(rotationZ_r)])
    #     ax.set_ylim([-Ly, Ly*(nRow+1) + displacementY_r])
    #     ax.grid(linestyle='-', linewidth='0.5')
    #     ax.set_aspect('equal')

    #     # prepare data for animation
    #     t = sol.t
    #     frames = len(t)
    #     target_frames = min(len(t), 100)
    #     frac = int(frames / target_frames)
    #     animation_time = 5
    #     interval = animation_time * 1000 / target_frames

    #     frames = target_frames
    #     t = t[::frac]
    #     q = sol.q[::frac]

    #     centerlines = []
    #     # lobj, = ax.plot([], [], '-k')
    #     for bdy in beams:
    #         lobj, = ax.plot([], [], '-k')
    #         centerlines.append(lobj)
            
    #     def animate(i):
    #         for idx, bdy in enumerate(beams):
    #                 # q_body = q[i][bdy.qDOF]
    #                 # r = []
    #                 # for i, xi in enumerate(xi_plt):
    #                 #     qp = q_body[bdy_qDOF_P[i]]
    #                 #     r.append(NN[i] @ qp)

    #                 # x, y = np.array(r).T
    #                 # centerlines[idx].set_data(x, y)

    #             x, y, _ = bdy.centerline(q[i], n=2).T
    #             centerlines[idx].set_data(x, y)

    #         return centerlines

    #     anim = animation.FuncAnimation(fig, animate, frames=frames, interval=interval, blit=False)

    #     plt.show()