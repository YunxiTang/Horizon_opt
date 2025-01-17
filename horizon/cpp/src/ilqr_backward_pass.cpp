#include "ilqr_impl.h"

struct HessianIndefinite : std::runtime_error
{
    using std::runtime_error::runtime_error;
};

void IterativeLQR::backward_pass()
{
    TIC(backward_pass);

    // initialize backward recursion from final cost..
    _value.back().S = _cost.back().Q();
    _value.back().s = _cost.back().q();

    // regularize final cost
    _value.back().S.diagonal().array() += _hxx_reg;

    // ..and initialize constraints and bounds
    _constraint_to_go->set(_constraint.back());

    if(_log) std::cout << "n_constr[" << _N << "] = " <<
                  _constraint_to_go->dim() << " (before bounds)\n";

    add_bound_constraint(_N);

    if(_log) std::cout << "n_constr[" << _N << "] = " <<
                  _constraint_to_go->dim() << "\n";

    // backward pass
    int i = _N - 1;
    while(i >= 0)
    {
        try
        {
            backward_pass_iter(i);
            --i;
        }
        catch(HessianIndefinite&)
        {
            increase_regularization();
            if(_verbose) std::cout << "increasing reg at k = " << i << ", hxx_reg = " << _hxx_reg << "\n";
            // retry with increased reg
            return backward_pass();
        }
    }

    // compute dx[0]
    optimize_initial_state();

    // here we should've treated all constraints
    if(_constraint_to_go->dim() > 0)
    {
        // some of them could be infeasible unless the initial
        // satisfies them already, let's check the residual
        // from the computed dx[0]
        Eigen::VectorXd residual;
        residual = _constraint_to_go->C()*_bp_res[0].dx +
                    _constraint_to_go->h();

        // infeasible warning
        if(residual.lpNorm<1>() > 1e-8)
        {

            std::cout << "warn at k = 0: " << _constraint_to_go->dim() <<
                         " constraints not satified, residual inf-norm is " <<
                         residual.lpNorm<Eigen::Infinity>() << "\n";

            if(_log)
            {
                std::cout << "C = \n" << _constraint_to_go->C().format(2) << "\n" <<
                             "h = " << _constraint_to_go->h().transpose().format(2) << "\n";
            }
        }

    }

}

void IterativeLQR::backward_pass_iter(int i)
{
    TIC(backward_pass_inner);

    // constraint handling
    // this will filter out any constraint that can't be
    // fullfilled with the current u_k, and needs to be
    // propagated to the previous time step
    auto constr_feas = handle_constraints(i);

    // num of feasible constraints
    const int nc = constr_feas.h.size();

    // intermediate cost
    const auto& cost = _cost[i];
    const auto r = cost.r();
    const auto q = cost.q();
    const auto& Q = cost.Q();
    const auto& R = cost.R();
    const auto& P = cost.P();

    // dynamics
    const auto& dyn = _dyn[i];
    const auto& A = dyn.A();
    const auto& B = dyn.B();
    const auto& d = dyn.d;

    // ..value function
    const auto& value_next = _value[i+1];
    const auto& Snext = value_next.S;
    const auto& snext = value_next.s;

    THROW_NAN(Snext);
    THROW_NAN(snext);

    // ..workspace
    auto& tmp = _tmp[i];
    auto& K = tmp.kkt;
    auto& kx0 = tmp.kx0;
    auto& u_lam = tmp.u_lam;

    // components of next node's value function
    TIC(form_value_fn_inner);
    tmp.s_plus_S_d.noalias() = snext + Snext*d;
    tmp.S_A.noalias() = Snext*A;

    tmp.hx.noalias() = q + A.transpose()*tmp.s_plus_S_d;
    tmp.Hxx.noalias() = Q + A.transpose()*tmp.S_A;
    tmp.Hxx.diagonal().array() += _hxx_reg;

    // remaining components of next node's value function
    tmp.hu.noalias() = r + B.transpose()*tmp.s_plus_S_d;
    tmp.Huu.noalias() = R + B.transpose()*Snext*B;
    tmp.Hux.noalias() = P + B.transpose()*tmp.S_A;
    tmp.Huu.diagonal().array() += _huu_reg;
    TOC(form_value_fn_inner);

    // print
    if(_log)
    {
        Eigen::VectorXd eigS = Snext.eigenvalues().real();
        std::cout << "eig(Hxx[" << i+1 << "]) in [" <<
                     eigS.minCoeff() << ", " << eigS.maxCoeff() << "] \n";

        Eigen::VectorXd eigHuu = tmp.Huu.eigenvalues().real();
        std::cout << "eig(Huu[" << i << "]) in [" <<
                     eigHuu.minCoeff() << ", " << eigHuu.maxCoeff() << "] \n";
    }

    // todo: second-order terms from dynamics

    // form kkt matrix
    TIC(form_kkt_inner);
    K.setZero(nc + _nu, nc + _nu);
    K.topLeftCorner(_nu, _nu) = tmp.Huu;
    K.topRightCorner(_nu, nc) = constr_feas.D.transpose();
    K.bottomLeftCorner(nc, _nu) = constr_feas.D;
    K.bottomRightCorner(nc, nc).diagonal().array() -= _kkt_reg;

    kx0.resize(_nu + nc, _nx + 1);
    kx0.leftCols(_nx) << -tmp.Hux,
                         -constr_feas.C;
    kx0.col(_nx) << -tmp.hu,
                    -constr_feas.h;
    TOC(form_kkt_inner);

    // solve kkt equation
    TIC(solve_kkt_inner);
    THROW_NAN(K);
    THROW_NAN(kx0);
    switch(_kkt_decomp_type)
    {
        case Lu:
            tmp.lu.compute(K);
            u_lam = tmp.lu.solve(kx0);
            break;

        case Qr:
            tmp.qr.compute(K);
            u_lam = tmp.qr.solve(kx0);
            break;

        case Ldlt:
            tmp.ldlt.compute(K);
            u_lam = tmp.ldlt.solve(kx0);
            break;

        default:
             throw std::invalid_argument("kkt decomposition supports only qr, lu, or ldlt");

    }

    if(_log)
    {
        std::cout << "kkt_err[" << i << "] = " <<
                     (K*u_lam - kx0).lpNorm<Eigen::Infinity>() << "\n";

        std::cout << "feas_constr[" << i << "] = " <<
                      nc << "\n";

        std::cout << "infeas_constr[" << i << "] = " <<
                     _constraint_to_go->dim() << "\n";
    }

    THROW_NAN(u_lam);
    TOC(solve_kkt_inner);

    // check
    if(!u_lam.allFinite() || u_lam.hasNaN())
    {
        throw HessianIndefinite("");
    }

    // save solution
    auto& res = _bp_res[i];
    auto& Lu = res.Lu;
    auto& lu = res.lu;
    auto& lam = res.glam;
    Lu = u_lam.topLeftCorner(_nu, _nx);
    lu = u_lam.col(_nx).head(_nu);
    lam = u_lam.col(_nx).tail(nc);

    // save optimal value function
    TIC(upd_value_fn_inner);
    auto& value = _value[i];
    auto& S = value.S;
    auto& s = value.s;

    S.noalias() = tmp.Hxx + Lu.transpose()*(tmp.Huu*Lu + tmp.Hux) + tmp.Hux.transpose()*Lu;
    S = 0.5*(S + S.transpose());  // note: symmetrize
    s.noalias() = tmp.hx + tmp.Hux.transpose()*lu + Lu.transpose()*(tmp.hu + tmp.Huu*lu);
    THROW_NAN(S);
    THROW_NAN(s);
    TOC(upd_value_fn_inner);

}

void IterativeLQR::optimize_initial_state()
{
    Eigen::VectorXd& dx = _bp_res[0].dx;
    Eigen::VectorXd& lam = _bp_res[0].dx_lam;

    // typical case: initial state is fixed
    if(fixed_initial_state())
    {
        dx = _x_lb.col(0) - state(0);
        return;
    }

    // cost
    auto& S = _value[0].S;
    auto& s = _value[0].s;

    // constraints and bounds
    auto C = _constraint_to_go->C();
    auto h = _constraint_to_go->h();

    // construct kkt matrix
    TIC(construct_state_kkt);
    Eigen::MatrixXd& K = _tmp[0].x_kkt;
    K.resize(s.size() + h.size(), s.size() + h.size());
    K.topLeftCorner(S.rows(), S.cols()) = S;
    K.topRightCorner(C.cols(), C.rows()) = C.transpose();
    K.bottomLeftCorner(C.rows(), C.cols()) = C;
    K.bottomRightCorner(C.rows(), C.rows()).setZero();
    TOC(construct_state_kkt);

    // residual vector
    Eigen::VectorXd k = _tmp[0].x_k0;
    k.resize(s.size() + h.size());
    k << -s,
         -h;

    THROW_NAN(K);
    THROW_NAN(k);

    // solve kkt equation
    TIC(solve_state_kkt);
    auto& lu = _tmp[0].x_lu;
    auto& qr = _tmp[0].x_qr;
    auto& ldlt = _tmp[0].x_ldlt;
    Eigen::VectorXd& dx_lam = _tmp[0].dx_lam;

    switch(_kkt_decomp_type)
    {
        case Lu:

            lu.compute(K);
            dx_lam = lu.solve(k);
            break;

        case Qr:

            dx_lam = qr.solve(k);
            break;

        case Ldlt:

            ldlt.compute(K);
            dx_lam = ldlt.solve(k);
            break;

        default:
             throw std::invalid_argument("kkt decomposition supports only qr, lu, or ldlt");

    }
    TOC(solve_state_kkt);
    THROW_NAN(dx_lam);

    if(_log)
    {
        std::cout << "state_kkt_err = " <<
                     (K*dx_lam - k).lpNorm<Eigen::Infinity>() << "\n";
    }

    // save solution
    dx = dx_lam.head(s.size());
    lam = dx_lam.tail(h.size());

    // check constraints
    Eigen::MatrixXd Cinf = C;
    Eigen::VectorXd hinf = h;

    _constraint_to_go->clear();

    for(int i = 0; i < hinf.size(); i++)
    {
        // feasible, do nothing
        if(std::fabs(Cinf.row(i)*dx + hinf[i]) <
                _constraint_violation_threshold)
        {
            continue;
        }

        // infeasible, add it back to constraint to go
        // this will generate an infeasibility warning
        _constraint_to_go->add(Cinf.row(i),
                               hinf.row(i));
    }
}

void IterativeLQR::add_bound_constraint(int k)
{
    Eigen::RowVectorXd x_ei, u_ei;

    // state bounds
    u_ei.setZero(_nu);
    for(int i = 0; i < _nx; i++)
    {
        // if initial state is fixed, don't add
        // constraints for it
        if(k == 0 && fixed_initial_state())
        {
            continue;
        }

        // equality
        if(_x_lb(i, k) == _x_ub(i, k))
        {
            x_ei = x_ei.Unit(_nx, i);

            Eigen::Matrix<double, 1, 1> hd;
            hd(0) = _xtrj(i, k) - _x_lb(i, k);

            _constraint_to_go->add(x_ei, u_ei, hd);

            if(_log)
            {
                std::cout << k << ": detected state equality constraint (index " <<
                             i << ", value = " << _x_lb(i, k) << ") \n";
            }

        }
    }

    // input bounds
    x_ei.setZero(_nx);
    for(int i = 0; i < _nu; i++)
    {
        if(k == _N)
        {
            break;
        }

        // equality
        if(_u_lb(i, k) == _u_ub(i, k))
        {
            u_ei = u_ei.Unit(_nu, i);

            Eigen::Matrix<double, 1, 1> hd;
            hd(0) = _utrj(i, k) - _u_lb(i, k);

            _constraint_to_go->add(x_ei, u_ei, hd);

            if(_log)
            {
                std::cout << k << ": detected input equality constraint (index " <<
                             i << ", value = " << _u_lb(i, k) << ") \n";
            }
        }
    }

}

bool IterativeLQR::auglag_update()
{
    // check if we need to update the aug lag estimate
    if(!_enable_auglag)
    {
        return false;
    }

    // current solution too coarse based on merit derivative
    if(std::fabs(_fp_res->merit_der) >
            _merit_der_threshold*(1 + _fp_res->merit))
    {
        return false;
    }

    // current solution does satisfy bounds,
    // we dont need to increase rho further
    if(_fp_res->bound_violation < _constraint_violation_threshold)
    {
        return false;
    }

    // grow rho
    _rho *= _rho_growth_factor;

    // update lag mult estimate
    for(int i = 0; i < _N + 1; i++)
    {
        _auglag_cost[i]->update_lam(
                    _xtrj.col(i),
                    _utrj.col(i),
                    i);

        _auglag_cost[i]->setRho(_rho);

        _lam_bound_x.col(i) = _auglag_cost[i]->getStateMultiplier();

        _lam_bound_u.col(i) = _auglag_cost[i]->getInputMultiplier();
    }

    _fp_res->mu_b = _lam_bound_u.lpNorm<1>() + _lam_bound_x.lpNorm<1>();

    std::cout << "[ilqr] performing auglag update \n";

    return true;
}

void IterativeLQR::increase_regularization()
{
    if(_hxx_reg < 1e-6)
    {
        _hxx_reg = 1.0;
    }

    _hxx_reg *= _hxx_reg_growth_factor;

    if(_hxx_reg < _hxx_reg_base)
    {
        _hxx_reg = _hxx_reg_base;
    }
}

void IterativeLQR::reduce_regularization()
{
    _hxx_reg /= std::pow(_hxx_reg_growth_factor, 1./3.);

    if(_hxx_reg < _hxx_reg_base)
    {
        _hxx_reg = _hxx_reg_base;
    }
}

IterativeLQR::FeasibleConstraint IterativeLQR::handle_constraints(int i)
{
    TIC(handle_constraints_inner);

    // some shorthands for..

    // ..dynamics
    auto& dyn = _dyn[i];
    const auto& A = dyn.A();
    const auto& B = dyn.B();
    const auto& d = dyn.d;  // note: has been computed during linearization phase

    // ..workspace
    auto& tmp = _tmp[i];
    auto& Cf = tmp.Cf;
    auto& Df = tmp.Df;
    auto& hf = tmp.hf;
    auto& cod = tmp.ccod;
    auto& qr = tmp.cqr;
    auto& svd = tmp.csvd;

    // ..backward pass result
    auto& res = _bp_res[i];

    TIC(constraint_prepare_inner);
    // back-propagate constraint to go from next step to current step
    _constraint_to_go->propagate_backwards(A, B, d);

    // add current step intermediate constraint
    _constraint_to_go->add(_constraint[i]);

    // add bounds
    add_bound_constraint(i);

    // number of constraints
    int nc = _constraint_to_go->dim();
    res.nc = nc;

    if(_log)
    {
        std::cout << "n_constr[" << i << "] = " <<
                      nc << "\n";
    }

    // no constraint to handle, do nothing
    if(nc == 0)
    {
        Cf.setZero(0, _nx);
        Df.setZero(0, _nu);
        hf.setZero(0);
        return FeasibleConstraint{Cf, Df, hf};
    }

    // decompose constraint into a feasible and infeasible components
    auto C = _constraint_to_go->C();
    auto D = _constraint_to_go->D();
    auto h = _constraint_to_go->h();
    TOC(constraint_prepare_inner);
    THROW_NAN(C);
    THROW_NAN(D);
    THROW_NAN(h);

    // cod of D
    TIC(constraint_decomp_inner);
    int rank = -1;
    switch(_constr_decomp_type)
    {
        case Cod:
            cod.setThreshold(_svd_threshold);
            cod.compute(D);
            rank = cod.rank();
            if(cod.maxPivot() < _svd_threshold)
            {
                rank = 0;
            }
            tmp.codQ = cod.matrixQ();
            break;

        case Qr:
            qr.setThreshold(_svd_threshold);
            qr.compute(D);
            rank = qr.rank();
            if(qr.maxPivot() < _svd_threshold)
            {
                rank = 0;
            }
            tmp.codQ = qr.matrixQ();
            if(_log)
            {
                std::cout << "matrixR diagonal entries = " <<
                qr.matrixR().diagonal().head(rank).transpose().format(2) << "\n";
            }
            break;

        case Svd:
            svd.setThreshold(_svd_threshold);
            svd.compute(D, Eigen::ComputeFullU);
            rank = svd.rank();
            if(svd.singularValues()[0] < _svd_threshold)
            {
                rank = 0;
            }
            tmp.codQ = svd.matrixU();
            break;

       default:
            throw std::invalid_argument("constraint decomposition supports only qr, svd, or cod");

    }

    THROW_NAN(tmp.codQ);
    MatConstRef codQ1 = tmp.codQ.leftCols(rank);
    MatConstRef codQ2 = tmp.codQ.rightCols(nc - rank);
    TOC(constraint_decomp_inner);

    // feasible part
    TIC(constraint_upd_to_go_inner);
    Cf.noalias() = codQ1.transpose()*C;
    Df.noalias() = codQ1.transpose()*D;
    hf.noalias() = codQ1.transpose()*h;

    // infeasible part
    Eigen::MatrixXd Cinf = codQ2.transpose()*C;
    Eigen::VectorXd hinf = codQ2.transpose()*h;

    _constraint_to_go->clear();

    for(int i = 0; i < hinf.size(); i++)
    {
        // i-th infeasible constraint is in the form 0x = 0
        if(std::fabs(hinf[i]) < 1e-9 &&
                Cinf.row(i).lpNorm<Eigen::Infinity>() < 1e-9)
        {
            std::cout << "warn at k = " << i << ": removing linearly dependent constraint \n";
            continue;
        }

        _constraint_to_go->add(Cinf.row(i),
                               hinf.row(i));
    }

    return FeasibleConstraint{Cf, Df, hf};

}

void IterativeLQR::compute_constrained_input(Temporaries& tmp, BackwardPassResult& res)
{
#if false
    if(_decomp_type == Qr)
    {
        TIC(compute_constrained_input_qr_inner);
        compute_constrained_input_qr(tmp, res);
    }
    else if(_decomp_type == Svd)
    {
        TIC(compute_constrained_input_svd_inner);
        compute_constrained_input_svd(tmp, res);
    }
    else
    {
        throw std::invalid_argument("invalid decomposition");
    }
#endif
}

void IterativeLQR::compute_constrained_input_svd(Temporaries& tmp, BackwardPassResult& res)
{
#if false
    auto C = _constraint_to_go->C();
    auto D = _constraint_to_go->D();
    auto h = _constraint_to_go->h();

    // some shorthands
    auto& svd = tmp.svd;
    auto& rotC = tmp.rotC;
    auto& roth = tmp.roth;
    auto& lc = tmp.lc;
    auto& Lc = tmp.Lc;
    auto& Bz = tmp.Bz;

    // svd of input matrix
    const double sv_ratio_thr = _svd_threshold;
    THROW_NAN(D);
    TIC(constraint_svd_inner);
    svd.compute(D, Eigen::ComputeFullU|Eigen::ComputeFullV);
    TOC(constraint_svd_inner);
    const auto& U = svd.matrixU();
    const auto& V = svd.matrixV();
    const auto& sv = svd.singularValues();
    THROW_NAN(svd.singularValues());
    THROW_NAN(svd.matrixU());
    THROW_NAN(svd.matrixV());
    svd.setThreshold(sv[0]*sv_ratio_thr);
    int rank = svd.rank();
    int ns_dim = _nu - rank;

    // rotate constraints
    rotC.noalias() = U.transpose()*C;
    roth.noalias() = U.transpose()*h;

    // compute component of control input due to constraints,
    // i.e. uc = Lc*x + +Lz*z + lc, where:
    //  *) lc = -V[:, 0:r]*sigma^-1*rot_h
    //  *) Lz = V[:, r:]
    //  *) Lc = -V[:, 0:r]*sigma^-1*rot_C
    TIC(constraint_input_inner);
    lc.noalias() = -V.leftCols(rank) * roth.head(rank).cwiseQuotient(sv.head(rank));
    Lc.noalias() = -V.leftCols(rank) * sv.head(rank).cwiseInverse().asDiagonal() * rotC.topRows(rank);
    Bz.noalias() = V.rightCols(ns_dim);
    TOC(constraint_input_inner);

    // compute lagrangian multipliers corresponding to the
    // satisfied component of the constraints, i.e.
    // lam = Gu*u + Gx*x + g, where:
    //  *) Gu = -U[:, :r]*sigma^-1*V[:, :r]'*Huu
    //  *) Gx = -U[:, :r]*sigma^-1*V[:, :r]'*Hux
    //  *) g = -U[:, :r]*sigma^-1*V[:, :r]'*hu
    // note: we only compute the g and Gu term (assume dx = 0)
    TIC(constraint_lagmul_inner);
    tmp.UrSinvVrT.noalias() = U.leftCols(rank)*sv.head(rank).cwiseInverse().asDiagonal()*V.leftCols(rank).transpose();
    res.glam = -tmp.UrSinvVrT*tmp.huf;
    res.Gu = -tmp.UrSinvVrT*tmp.Huuf;
    // res.Gx = -tmp.UrSinvVrT*tmp.Huxf;
    TOC(constraint_lagmul_inner);

    // remove satisfied constraints from constraint to go
    int nc = _constraint_to_go->dim();
    _constraint_to_go->set(rotC.bottomRows(nc - rank),
                           roth.tail(nc - rank));
#endif
}


void IterativeLQR::compute_constrained_input_qr(Temporaries &tmp, BackwardPassResult &res)
{
#if false
    auto C = _constraint_to_go->C();
    auto D = _constraint_to_go->D();
    auto h = _constraint_to_go->h();

    THROW_NAN(C);
    THROW_NAN(D);
    THROW_NAN(h);

    auto& qr = tmp.qr;
    auto& lc = tmp.lc;
    auto& Lc = tmp.Lc;
    auto& Bz = tmp.Bz;

    // D is fat (we can satisfy all constraints unless rank deficient, and there is
    // a nullspace control input to further minimize the cost)
    if(D.rows() < D.cols())
    {
        // D' = QR
        TIC(constraint_qr_inner);
        qr.compute(D.transpose());
        TOC(constraint_qr_inner);
        Eigen::MatrixXd r = qr.matrixQR().triangularView<Eigen::Upper>();
        THROW_NAN(r);

        // rank estimate
        int rank = D.rows();

        for(int i = D.rows()-1; i >= 0; --i)
        {
            if(std::fabs(r(i, i)) < 1e-9)
            {
                --rank;
            }
            else
            {
                break;
            }
        }

        // rank deficient case?
        int rank_def = D.rows() - rank;

        // nullspace left after constraint
        int ns_dim = D.cols() - rank;

        // r = [r1; 0] =  [r11, r12; 0]
        Eigen::MatrixXd r1 = r.topRows(rank);
        Eigen::MatrixXd r11 = r1.leftCols(rank);
        Eigen::MatrixXd r12 = r1.rightCols(rank_def);

        // q = [q1, q2]
        TIC(constraint_qr_get_q_inner);
        Eigen::MatrixXd q = qr.householderQ();
        TOC(constraint_qr_get_q_inner);
        Eigen::MatrixXd q1 = q.leftCols(rank);
        Eigen::MatrixXd q2 = q.rightCols(ns_dim);
        THROW_NAN(q);

        // we must permute C and h in order to comply
        // with the qr column-pivoting
        Eigen::MatrixXd Cp = qr.colsPermutation().transpose()*C;
        Eigen::VectorXd hp = qr.colsPermutation().transpose()*h;

        // C = [C1; C2], h = [h1; h2]
        auto C1 = Cp.topRows(rank);
        auto C2 = Cp.bottomRows(rank_def);
        auto h1 = hp.head(rank);
        auto h2 = hp.tail(rank_def);

        // r11^-T
        TIC(constraint_r11_inv_T_inner);
        Eigen::MatrixXd r11_t_inv;
        r11_t_inv.setIdentity(rank, rank);
        r11.triangularView<Eigen::Upper>().solveInPlace(r11_t_inv);
        r11_t_inv.transposeInPlace();
        TOC(constraint_r11_inv_T_inner);
        THROW_NAN(r11_t_inv);

        // q1*r1^-T
        TIC(constraint_input_inner);
        Eigen::MatrixXd q1_r11_t_inv;
        q1_r11_t_inv.noalias() = q1*r11_t_inv;

        // compute input
        lc.noalias() = -q1_r11_t_inv*h1;
        Lc.noalias() = -q1_r11_t_inv*C1;
        Bz = q2;
        TOC(constraint_input_inner);

        // compute lag mul
        TIC(constraint_lagmul_inner);
        res.Gu.noalias() = -q1_r11_t_inv.transpose()*tmp.Huuf;
        res.glam.noalias() = -q1_r11_t_inv.transpose()*tmp.huf;
        TOC(constraint_lagmul_inner);

        // compute unsatisfied constraint portion
        Eigen::MatrixXd Cu, r12_t_r11_t_inv;
        Eigen::VectorXd hu;
        r12_t_r11_t_inv.noalias() = r12.transpose()*r11_t_inv;
        Cu.noalias() = C2 - r12_t_r11_t_inv*C1;
        hu.noalias() = h2 - r12_t_r11_t_inv*h1;

        // set unsatisfied constraints to current constraint to go
        _constraint_to_go->set(Cu, hu);
    }
    // D is tall, we can satisfy at most $rank constraints and the rest
    // must be propagated backwards in time
    else
    {
        // D = QR = Q1*R1
        TIC(constraint_qr_inner);
        qr.compute(D);
        TOC(constraint_qr_inner);

        // rank estimate
        int rank = D.cols();
        Eigen::MatrixXd  r = qr.matrixQR().triangularView<Eigen::Upper>();
        THROW_NAN(r);

        for(int i = D.cols()-1; i >= 0; --i)
        {
            if(std::fabs(r(i, i)) < 1e-9)
            {
                --rank;
            }
            else
            {
                break;
            }
        }

        // rank deficient case creates a nullspace
        int ns_dim = D.cols() - rank;

        // unsatisfied constraints
        int nc_left = D.rows() - rank;

        // r = [r1; 0]
        Eigen::MatrixXd r1 = r.topRows(rank);
        Eigen::MatrixXd r11 = r1.leftCols(rank);
        Eigen::MatrixXd r12 = r1.rightCols(ns_dim);

        // q = [q1, q2]
        TIC(constraint_qr_get_q_inner);
        Eigen::MatrixXd q = qr.householderQ();
        TOC(constraint_qr_get_q_inner);
        Eigen::MatrixXd q1 = q.leftCols(rank);
        Eigen::MatrixXd q2 = q.rightCols(nc_left);
        THROW_NAN(q);

        // r1^-1
        TIC(constraint_r11_inv_inner);
        Eigen::MatrixXd r11_inv;
        r11_inv.setIdentity(rank, rank);
        r11.triangularView<Eigen::Upper>().solveInPlace(r11_inv);
        TOC(constraint_r11_inv_inner);
        THROW_NAN(r11_inv);

        // r1^-1*q1^T
        TIC(constraint_input_inner);
        Eigen::MatrixXd r11_inv_q1_t;
        r11_inv_q1_t.noalias() = r11_inv*q1.transpose();

        // compute input
        lc.resize(_nu);
        lc.head(rank).noalias() = -r11_inv_q1_t*h;
        lc.array().tail(ns_dim) = 0;
        Lc.resize(_nu, _nx);
        Lc.topRows(rank).noalias() = -r11_inv_q1_t*C;
        Lc.array().bottomRows(ns_dim) = 0;
        Bz.resize(_nu, ns_dim);
        Bz.topRows(rank).noalias() = -r11_inv*r12;
        Bz.bottomRows(ns_dim).setIdentity(ns_dim, ns_dim);

        // apply permutation to input in order to comply
        // with qr column pivoting
        lc = qr.colsPermutation()*lc;
        Lc = qr.colsPermutation()*Lc;
        Bz = qr.colsPermutation()*Bz;
        TOC(constraint_input_inner);

        // compute lagrangian multiplier
        TIC(constraint_lagmul_inner);
        res.Gu.noalias() = -r11_inv.transpose()*tmp.Huuf.topRows(rank);
        res.glam.noalias() = -r11_inv.transpose()*tmp.huf.head(rank);
        TOC(constraint_lagmul_inner);

        // set unsatisfied constraints to current constraint to go
        _constraint_to_go->set(q2.transpose()*C, q2.transpose()*h);

    }
#endif
}



