import pandas as pd
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
from sklearn.preprocessing import StandardScaler
import matplotlib.pyplot as plt
import argparse
import os
from tqdm import tqdm

class ExpressionDataset(Dataset):
    def __init__(self, expressions, times=None, celltypes=None):
        self.expressions = torch.FloatTensor(expressions)
        self.times = torch.FloatTensor(times) if times is not None else None
        self.celltypes = celltypes
        
    def __len__(self):
        return len(self.expressions)
    
    def __getitem__(self, idx):
        sample = {'expression': self.expressions[idx]}
        if self.times is not None:
            sample['time'] = self.times[idx]
        if self.celltypes is not None:
            sample['celltype'] = self.celltypes[idx]
        return sample

class PhaseAutoEncoder(nn.Module):
    def __init__(self, input_dim, dropout=0.2, hidden_dim=128):
        super(PhaseAutoEncoder, self).__init__()
        self.input_dim = input_dim

        # self.encoder = nn.Sequential(
        #     nn.Linear(input_dim, hidden_dim),
        #     nn.ReLU(),
        #     nn.Dropout(dropout),
        #     nn.Linear(hidden_dim, 32),
        #     nn.ReLU(),
        #     nn.Linear(32, 2)
        # )
        self.encoder = nn.Linear(input_dim, 2)
        # self.decoder = nn.Sequential(
        #     nn.Linear(2, 32),
        #     nn.ReLU(),
        #     nn.Linear(32, hidden_dim),
        #     nn.ReLU(),
        #     nn.Dropout(dropout),
        #     nn.Linear(hidden_dim, input_dim)
        # )
        self.decoder = nn.Linear(2, input_dim)
    
    def forward(self, x):
        raw_coords = self.encoder(x)
        norm = torch.norm(raw_coords, dim=1, keepdim=True) + 1e-8
        unit_coords = raw_coords / norm
        reconstructed = self.decoder(unit_coords)
        return unit_coords, reconstructed
    
    def encode(self, x):
        return self.encoder(x)
    
    def decode(self, phase_coords):
        return self.decoder(phase_coords)


class SineParameterPredictor(nn.Module):
    def __init__(self, input_dim, hidden_dim=64, dropout=0.1):
        super(SineParameterPredictor, self).__init__()
        
        self.network = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim // 2, 3)
        )
        
        self.amplitude_activation = nn.Softplus()
        self.phase_activation = nn.Tanh()
        
    def forward(self, gene_expressions):
        raw_output = self.network(gene_expressions)
        
        amplitude = self.amplitude_activation(raw_output[:, 0:1]) + 1e-6
        phase_shift = self.phase_activation(raw_output[:, 1:2]) * np.pi
        baseline = raw_output[:, 2:3]
        
        return torch.cat([amplitude, phase_shift, baseline], dim=1)

def coords_to_phase(coords):
    x, y = coords[:, 0], coords[:, 1]
    phase = torch.atan2(y, x)
    phase = torch.where(phase < 0, phase + 2*np.pi, phase)
    return phase

def phase_to_coords(phase):
    x = torch.cos(phase)
    y = torch.sin(phase)
    return torch.stack([x, y], dim=1)

def time_to_phase(time_hours, period_hours=24.0):
    return 2 * np.pi * time_hours / period_hours

def load_and_preprocess_train_data(train_file, n_components=50, max_samples=100, random_state=42):
    print("=== 加载训练数据 ===")
    df = pd.read_csv(train_file, low_memory=False)
    
    celltype_row = df[df['Gene_Symbol'] == 'celltype_D']
    time_row = df[df['Gene_Symbol'] == 'time_C']
    
    has_celltype = not celltype_row.empty
    has_time = not time_row.empty
    
    print(f"训练集包含时间信息: {has_time}")
    print(f"训练集包含细胞类型信息: {has_celltype}")
    
    sample_columns = [col for col in df.columns if col != 'Gene_Symbol']
    n_samples = len(sample_columns)
    
    print(f"原始样本数量: {n_samples}")
    print(f"最大样本数量限制: {max_samples}")
    
    celltypes = None
    times = None
    
    if has_celltype:
        celltypes = celltype_row.iloc[0][sample_columns].values
        print(f"训练集细胞类型: {np.unique(celltypes)}")
    
    if has_time:
        times = time_row.iloc[0][sample_columns].values.astype(float)
        print(f"训练集时间范围: {times.min():.2f} - {times.max():.2f} 小时")
    
    gene_df = df[~df['Gene_Symbol'].isin(['celltype_D', 'time_C'])].copy()
    gene_names = gene_df['Gene_Symbol'].values
    expression_data = gene_df[sample_columns].values.T
    
    print(f"训练集原始基因数量: {len(gene_names)}")
    
    print("进行训练数据标准化...")
    scaler = StandardScaler()
    expression_scaled = scaler.fit_transform(expression_data)
    
    print(f"基于训练集进行奇异值分解，选择前 {n_components} 个最重要的基因...")
    U, s, Vt = np.linalg.svd(expression_scaled.T, full_matrices=False)
    
    n_top_components = min(n_components, len(s))
    gene_importance = np.sum(np.abs(U[:, :n_top_components]) * s[:n_top_components], axis=1)
    
    top_gene_indices = np.argsort(gene_importance)[-n_components:][::-1]
    selected_genes = gene_names[top_gene_indices]
    selected_expression = expression_scaled[:, top_gene_indices]
    
    if n_samples > max_samples:
        print(f"样本数量 ({n_samples}) 超过最大限制 ({max_samples})，进行截断...")
        np.random.seed(random_state)
        selected_indices = np.random.choice(n_samples, max_samples, replace=False)
        selected_indices = np.sort(selected_indices)
        
        selected_expression = selected_expression[selected_indices]
        if times is not None:
            times = times[selected_indices]
        if celltypes is not None:
            celltypes = celltypes[selected_indices]
        
        actual_samples = max_samples
        print(f"截断后样本数量: {actual_samples}")
        
    elif n_samples < max_samples:
        print(f"样本数量 ({n_samples}) 少于最大限制 ({max_samples})，进行0填充...")
        pad_size = max_samples - n_samples
        
        padding = np.zeros((pad_size, n_components))
        selected_expression = np.vstack([selected_expression, padding])
        
        if times is not None:
            times = np.concatenate([times, np.zeros(pad_size)])
        if celltypes is not None:
            celltypes = np.concatenate([celltypes, ['PADDING'] * pad_size])
        
        actual_samples = max_samples
        print(f"填充后样本数量: {actual_samples}")
        
    else:
        actual_samples = n_samples
        print(f"样本数量正好等于最大限制: {actual_samples}")
    
    print(f"最终使用的样本数量: {actual_samples}")
    print(f"选择的基因数量: {len(selected_genes)}")
    print(f"选择的基因样例: {selected_genes[:10].tolist()}")
    
    train_dataset = ExpressionDataset(selected_expression, times, celltypes)
    
    preprocessing_info = {
        'scaler': scaler,
        'selected_gene_indices': top_gene_indices,
        'selected_genes': selected_genes,
        'gene_importance_scores': gene_importance[top_gene_indices],
        'all_gene_names': gene_names,
        'train_has_time': has_time,
        'train_has_celltype': has_celltype,
        'n_components': n_components,
        'max_samples': max_samples,
        'actual_samples': actual_samples,
        'original_samples': n_samples,
        'svd_info': {
            'U': U[:, :n_top_components],
            's': s[:n_top_components],
            'Vt': Vt[:n_top_components, :]
        }
    }
    
    return train_dataset, preprocessing_info

def load_and_preprocess_test_data(test_file, preprocessing_info):
    print("\n=== 加载测试数据 ===")
    df = pd.read_csv(test_file, low_memory=False)
    
    celltype_row = df[df['Gene_Symbol'] == 'celltype_D']
    time_row = df[df['Gene_Symbol'] == 'time_C']
    
    has_celltype = not celltype_row.empty
    has_time = not time_row.empty
    
    print(f"测试集包含时间信息: {has_time}")
    print(f"测试集包含细胞类型信息: {has_celltype}")
    
    sample_columns = [col for col in df.columns if col != 'Gene_Symbol']
    n_samples = len(sample_columns)
    
    celltypes = None
    times = None
    
    if has_celltype:
        celltypes = celltype_row.iloc[0][sample_columns].values
        print(f"测试集细胞类型: {np.unique(celltypes)}")
    
    if has_time:
        times = time_row.iloc[0][sample_columns].values.astype(float)
        print(f"测试集时间范围: {times.min():.2f} - {times.max():.2f} 小时")
    
    gene_df = df[~df['Gene_Symbol'].isin(['celltype_D', 'time_C'])].copy()
    test_gene_names = gene_df['Gene_Symbol'].values
    test_expression_data = gene_df[sample_columns].values.T
    
    print(f"测试集原始基因数量: {len(test_gene_names)}")
    print(f"测试集样本数量: {n_samples}")
    
    scaler = preprocessing_info['scaler']
    selected_genes = preprocessing_info['selected_genes']
    n_components = preprocessing_info['n_components']
    
    print("使用训练集的标准化参数处理测试数据...")
    test_expression_scaled = scaler.transform(test_expression_data)

    test_selected_expression = np.zeros((n_samples, n_components))
    missing_genes = []
    found_genes = []

    for train_idx, gene in enumerate(selected_genes):
        if gene in test_gene_names:
            test_gene_idx = np.where(test_gene_names == gene)[0][0]
            test_selected_expression[:, train_idx] = test_expression_scaled[:, test_gene_idx]
            found_genes.append(gene)
        else:
            missing_genes.append(gene)
            test_selected_expression[:, train_idx] = 0
    
    print(f"测试集中找到的基因数量: {len(found_genes)}")
    if missing_genes:
        print(f"测试集中缺失的基因数量: {len(missing_genes)}")
        print(f"缺失基因样例: {missing_genes[:5]}")
    
    test_dataset = ExpressionDataset(test_selected_expression, times, celltypes)
    
    test_preprocessing_info = preprocessing_info.copy()
    test_preprocessing_info.update({
        'test_has_time': has_time,
        'test_has_celltype': has_celltype,
        'test_sample_columns': sample_columns,
        'found_genes': found_genes,
        'missing_genes': missing_genes
    })
    
    return test_dataset, test_preprocessing_info

def neural_sine_fitting_loss(phase_coords, expressions, celltypes, sine_predictor, 
                           lambda_neural_sine=1.0, min_samples=5):
    
    if celltypes is None or expressions is None:
        return torch.tensor(0.0, device=phase_coords.device)
    
    device = phase_coords.device
    total_loss = torch.tensor(0.0, device=device)
    total_combinations = 0
    
    phases = coords_to_phase(phase_coords)
    
    unique_celltypes = np.unique(celltypes)
    n_genes = expressions.shape[1]
    input_dim = expressions.shape[0]
    
    for celltype in unique_celltypes:
        if celltype == 'PADDING':
            continue
            
        celltype_mask = np.array([ct == celltype for ct in celltypes])
        if celltype_mask.sum() < min_samples:
            continue
            
        celltype_phases = phases[celltype_mask]
        celltype_expressions = expressions[celltype_mask]
        
        for gene_idx in range(n_genes):
            gene_expression = celltype_expressions[:, gene_idx]
            
            if len(gene_expression) < min_samples:
                continue
            
            try:
                gene_input_full = torch.zeros((1, input_dim), device=gene_expression.device)
                gene_input_full[0, :len(gene_expression)] = gene_expression

                predicted_params = sine_predictor(gene_input_full)

                valid_mask = gene_input_full[0, :] != 0
                amplitude = predicted_params[0, 0]
                phase_shift = predicted_params[0, 1] 
                baseline = predicted_params[0, 2]

                predicted_sine = amplitude * torch.sin(celltype_phases + phase_shift) + baseline

                gene_loss = torch.mean((gene_expression[valid_mask[:len(gene_expression)]] - predicted_sine[valid_mask[:len(gene_expression)]]) ** 2)

                reg_loss = 0.01 * (amplitude ** 2 + baseline ** 2)

                total_loss += gene_loss + reg_loss
                total_combinations += 1
                
            except Exception as e:
                print(f"Warning: Failed to predict sine parameters for gene {gene_idx}, celltype {celltype}: {e}")
                continue
    
    if total_combinations > 0:
        return lambda_neural_sine * (total_loss / total_combinations)
    else:
        return torch.tensor(0.0, device=device)


def time_supervision_loss(phase_coords, true_times, lambda_time=1.0, period_hours=24.0):
    if true_times is None:
        return torch.tensor(0.0, device=phase_coords.device)
    
    true_phases = time_to_phase(true_times, period_hours)
    
    pred_phases = coords_to_phase(phase_coords)
    
    phase_diff = torch.abs(pred_phases - true_phases)
    phase_diff = torch.min(phase_diff, 2*np.pi - phase_diff)
    
    return lambda_time * torch.mean(phase_diff)


def train_model(model, sine_predictor, train_dataset, preprocessing_info, 
                                num_epochs=100, lr=0.001, device='cuda',
                                lambda_recon=1.0, lambda_time=0.5, lambda_neural_sine=0.1,
                                period_hours=24.0, save_dir='./model_checkpoints'):
    model = model.to(device)
    sine_predictor = sine_predictor.to(device)
    
    optimizer = optim.Adam(list(model.parameters()) + list(sine_predictor.parameters()), lr=lr)
    scheduler = optim.lr_scheduler.StepLR(optimizer, step_size=500, gamma=0.5)
    
    recon_criterion = nn.MSELoss()
    
    train_losses = []
    
    os.makedirs(save_dir, exist_ok=True)
    
    print("准备训练数据...")
    all_expressions = []
    all_times = []
    all_celltypes = []
    valid_mask = []
    
    for i in range(len(train_dataset)):
        sample = train_dataset[i]
        all_expressions.append(sample['expression'])
        
        if 'celltype' in sample and sample['celltype'] == 'PADDING':
            is_valid = False
        else:
            is_valid = True
        valid_mask.append(is_valid)
        
        if 'time' in sample:
            all_times.append(sample['time'])
        if 'celltype' in sample:
            all_celltypes.append(sample['celltype'])
    
    expressions_tensor = torch.stack(all_expressions).to(device)
    valid_mask_tensor = torch.tensor(valid_mask, device=device)
    
    times_tensor = None
    if all_times:
        times_tensor = torch.stack(all_times).to(device)
    
    celltypes_array = None
    if all_celltypes:
        celltypes_array = np.array(all_celltypes)
    
    print(f"训练数据准备完成:")
    print(f"  - 总样本数: {len(expressions_tensor)}")
    print(f"  - 有效样本数: {valid_mask_tensor.sum().item()}")
    
    print(f"\n=== 开始端到端联合训练 ({num_epochs} epochs) ===")
    
    with tqdm(total=num_epochs, desc="Joint Training Progress") as pbar:
        for epoch in range(num_epochs):
            model.train()
            sine_predictor.train()
            
            optimizer.zero_grad()
            
            phase_coords, reconstructed = model(expressions_tensor)
            
            if valid_mask_tensor.sum() > 0:
                valid_expressions = expressions_tensor[valid_mask_tensor]
                valid_reconstructed = reconstructed[valid_mask_tensor]
                recon_loss = recon_criterion(valid_reconstructed, valid_expressions)
            else:
                recon_loss = torch.tensor(0.0, device=device)
            
            time_loss = torch.tensor(0.0, device=device)
            if preprocessing_info['train_has_time'] and times_tensor is not None:
                valid_phase_coords = phase_coords[valid_mask_tensor]
                valid_times = times_tensor[valid_mask_tensor]
                if len(valid_times) > 0:
                    time_loss = time_supervision_loss(valid_phase_coords, valid_times, 1.0, period_hours)
            
            neural_sine_loss = torch.tensor(0.0, device=device)
            if preprocessing_info['train_has_celltype'] and celltypes_array is not None:
                valid_phase_coords = phase_coords[valid_mask_tensor]
                valid_expressions = expressions_tensor[valid_mask_tensor]
                valid_celltypes = celltypes_array[valid_mask_tensor.cpu().numpy()]
                non_padding_mask = valid_celltypes != 'PADDING'
                if non_padding_mask.sum() > 0:
                    final_phase_coords = valid_phase_coords[non_padding_mask]
                    final_expressions = valid_expressions[non_padding_mask]
                    final_celltypes = valid_celltypes[non_padding_mask]
                    
                    neural_sine_loss = neural_sine_fitting_loss(
                        final_phase_coords, final_expressions, final_celltypes, 
                        sine_predictor, 1.0
                    )

            total_loss = lambda_recon * recon_loss + lambda_time * time_loss + lambda_neural_sine * neural_sine_loss
            total_loss.backward()
            optimizer.step()
            
            train_losses.append(total_loss.item())
            
            scheduler.step()
            
            if (epoch + 1) % 10 == 0:
                pbar.set_postfix({
                    'Train loss': f'{total_loss.item():.4f}',
                    'Recon': f'{recon_loss.item():.4f}',
                    'Time': f'{time_loss.item():.4f}',
                    'Sine': f'{neural_sine_loss.item():.4f}',
                    'LR': f'{scheduler.get_last_lr()[0]:.6f}'
                })
            
            pbar.update(1)
    
    final_checkpoint = {
        'model_state_dict': model.state_dict(),
        'preprocessing_info': preprocessing_info,
        'train_losses': train_losses
    }
    torch.save(final_checkpoint, os.path.join(save_dir, 'final_model.pth'))
    
    return train_losses

def predict_and_save_phases(model, test_loader, preprocessing_info, device='cuda', save_dir='./results'):
    print("\n=== 预测测试集相位 ===")
    model.eval()
    
    all_phase_coords = []
    all_phases = []
    all_times = []
    all_celltypes = []
    sample_indices = []
    
    batch_start_idx = 0
    
    with torch.no_grad():
        for batch in test_loader:
            expressions = batch['expression'].to(device)
            times = batch.get('time', None)
            celltypes = batch.get('celltype', None)
            
            phase_coords, _ = model(expressions)
            
            phases = coords_to_phase(phase_coords)
            
            all_phase_coords.append(phase_coords.cpu().numpy())
            all_phases.append(phases.cpu().numpy())
            
            batch_size = expressions.shape[0]
            batch_indices = list(range(batch_start_idx, batch_start_idx + batch_size))
            sample_indices.extend(batch_indices)
            batch_start_idx += batch_size
            
            if times is not None:
                all_times.append(times.cpu().numpy())
            if celltypes is not None:
                all_celltypes.extend(celltypes)
    
    phase_coords = np.vstack(all_phase_coords)
    phases = np.concatenate(all_phases)
    
    if all_times:
        times = np.concatenate(all_times)
    else:
        times = None
        
    if all_celltypes:
        celltypes = np.array(all_celltypes)
    else:
        celltypes = None
    
    os.makedirs(save_dir, exist_ok=True)
    
    results_data = {
        'Sample_Index': sample_indices,
        'Phase_X': phase_coords[:, 0],
        'Phase_Y': phase_coords[:, 1],
        'Predicted_Phase_Radians': phases,
        'Predicted_Phase_Degrees': phases * 180 / np.pi,
        'Predicted_Phase_Hours': phases * preprocessing_info.get('period_hours', 24.0) / (2 * np.pi)
    }
    
    if times is not None:
        results_data['True_Time_Hours'] = times
        results_data['True_Phase_Radians'] = time_to_phase(times, preprocessing_info.get('period_hours', 24.0))
        results_data['Phase_Error_Radians'] = np.abs(phases - results_data['True_Phase_Radians'])
        results_data['Phase_Error_Radians'] = np.minimum(
            results_data['Phase_Error_Radians'], 
            2*np.pi - results_data['Phase_Error_Radians']
        )
        results_data['Phase_Error_Hours'] = results_data['Phase_Error_Radians'] * preprocessing_info.get('period_hours', 24.0) / (2 * np.pi)
    
    if celltypes is not None:
        results_data['Cell_Type'] = celltypes
    
    results_df = pd.DataFrame(results_data)
    
    predictions_file = os.path.join(save_dir, 'phase_predictions.csv')
    results_df.to_csv(predictions_file, index=False)
    print(f"详细预测结果保存到: {predictions_file}")
    
    simple_results = results_df[['Sample_Index', 'Predicted_Phase_Hours']].copy()
    simple_results.columns = ['Sample_ID', 'Predicted_Phase_Hours']
    
    simple_file = os.path.join(save_dir, 'phase_predictions_simple.csv')
    simple_results.to_csv(simple_file, index=False)
    print(f"简化预测结果保存到: {simple_file}")
    
    print(f"\n=== 预测统计 ===")
    print(f"预测样本数量: {len(phases)}")
    print(f"预测相位范围: {phases.min():.3f} - {phases.max():.3f} 弧度")
    print(f"预测相位范围: {(phases * 180 / np.pi).min():.1f} - {(phases * 180 / np.pi).max():.1f} 度")
    print(f"预测时间范围: {results_data['Predicted_Phase_Hours'].min():.2f} - {results_data['Predicted_Phase_Hours'].max():.2f} 小时")
    
    if times is not None:
        mean_error_hours = np.mean(results_data['Phase_Error_Hours'])
        std_error_hours = np.std(results_data['Phase_Error_Hours'])
        print(f"平均预测误差: {mean_error_hours:.2f} ± {std_error_hours:.2f} 小时")
        
        for threshold in [1, 2, 3, 6]:
            accuracy = np.mean(results_data['Phase_Error_Hours'] <= threshold) * 100
            print(f"误差 ≤ {threshold}小时的样本比例: {accuracy:.1f}%")
    
    if celltypes is not None:
        print(f"\n按细胞类型统计:")
        celltype_stats = results_df.groupby('Cell_Type').agg({
            'Predicted_Phase_Hours': ['mean', 'std', 'count']
        }).round(2)
        print(celltype_stats)
    
    create_prediction_plots(results_df, save_dir)
    
    return results_df

def create_prediction_plots(results_df, save_dir):
    
    plt.figure(figsize=(12, 8))
    
    plt.subplot(2, 2, 1)
    plt.hist(results_df['Predicted_Phase_Hours'], bins=24, alpha=0.7, edgecolor='black')
    plt.xlabel('Predicted Phase (Hours)')
    plt.ylabel('Frequency')
    plt.title('Distribution of Predicted Phases')
    plt.grid(True, alpha=0.3)
    
    plt.subplot(2, 2, 2)
    plt.scatter(results_df['Phase_X'], results_df['Phase_Y'], alpha=0.6)
    plt.xlabel('Phase X')
    plt.ylabel('Phase Y')
    plt.title('Phase Distribution in Unit Circle')
    plt.axis('equal')
    plt.grid(True, alpha=0.3)
    
    circle = plt.Circle((0, 0), 1, fill=False, color='red', linestyle='--', alpha=0.5)
    plt.gca().add_patch(circle)
    
    if 'True_Time_Hours' in results_df.columns:
        plt.subplot(2, 2, 3)
        plt.scatter(results_df['True_Time_Hours'], results_df['Predicted_Phase_Hours'], alpha=0.6)
        plt.plot([0, 24], [0, 24], 'r--', label='Perfect Prediction')
        plt.xlabel('True Time (Hours)')
        plt.ylabel('Predicted Phase (Hours)')
        plt.title('True Time vs Predicted Phase')
        plt.legend()
        plt.grid(True, alpha=0.3)
        
        plt.subplot(2, 2, 4)
        plt.hist(results_df['Phase_Error_Hours'], bins=20, alpha=0.7, edgecolor='black')
        plt.xlabel('Prediction Error (Hours)')
        plt.ylabel('Frequency')
        plt.title('Distribution of Prediction Errors')
        plt.grid(True, alpha=0.3)
    else:
        if 'Cell_Type' in results_df.columns:
            plt.subplot(2, 2, 3)
            unique_celltypes = results_df['Cell_Type'].unique()
            colors = plt.cm.tab10(np.linspace(0, 1, len(unique_celltypes)))
            
            for i, celltype in enumerate(unique_celltypes):
                mask = results_df['Cell_Type'] == celltype
                plt.scatter(results_df.loc[mask, 'Phase_X'], 
                          results_df.loc[mask, 'Phase_Y'], 
                          c=[colors[i]], label=celltype, alpha=0.6)
            
            plt.xlabel('Phase X')
            plt.ylabel('Phase Y')
            plt.title('Phase Distribution by Cell Type')
            plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
            plt.axis('equal')
            plt.grid(True, alpha=0.3)
            
            circle = plt.Circle((0, 0), 1, fill=False, color='red', linestyle='--', alpha=0.5)
            plt.gca().add_patch(circle)
    
    plt.tight_layout()
    plt.savefig(os.path.join(save_dir, 'prediction_analysis.png'), dpi=300, bbox_inches='tight')
    plt.close()
    
    print(f"预测分析图表保存到: {os.path.join(save_dir, 'prediction_analysis.png')}")


def get_original_gene_expressions(test_file, custom_genes, preprocessing_info, phase_hours_data, celltypes_data):
    try:
        print(f"从原始文件重新读取自定义基因: {custom_genes}")
        
        df = pd.read_csv(test_file, low_memory=False)
        
        gene_df = df[~df['Gene_Symbol'].isin(['celltype_D', 'time_C'])].copy()
        test_gene_names = gene_df['Gene_Symbol'].values
        
        sample_columns = [col for col in df.columns if col != 'Gene_Symbol']
        test_expression_data = gene_df[sample_columns].values.T
        
        scaler = preprocessing_info['scaler']
        test_expression_scaled = scaler.transform(test_expression_data)
        
        found_genes = []
        gene_expressions_list = []
        
        for gene in custom_genes:
            if gene in test_gene_names:
                gene_idx = np.where(test_gene_names == gene)[0][0]
                gene_expression = test_expression_scaled[:, gene_idx]
                gene_expressions_list.append(gene_expression)
                found_genes.append(gene)
                print(f"  ✓ 找到基因: {gene}")
            else:
                print(f"  ✗ 基因 {gene} 不在测试数据中")
        
        if len(found_genes) == 0:
            print("错误: 没有找到任何指定的基因")
            return None, None
        
        gene_expressions = np.column_stack(gene_expressions_list)
        
        print(f"成功获取 {len(found_genes)} 个基因的表达数据")
        print(f"表达数据维度: {gene_expressions.shape}")
        
        return gene_expressions, np.array(found_genes)
        
    except Exception as e:
        print(f"从原始文件读取基因表达数据时出错: {e}")
        return None, None

def plot_celltype_gene_expression_raw(expressions, phase_hours, gene_names, celltype, save_dir):
    n_genes = len(gene_names)
    
    print(f"为细胞类型 {celltype} 绘制基因表达图（原始数据）")
    print(f"样本数量: {len(expressions)}")
    print(f"基因数量: {n_genes}")
    print(f"表达数据维度: {expressions.shape}")
    
    n_cols = min(5, n_genes)
    n_rows = (n_genes + n_cols - 1) // n_cols
    
    _, axes = plt.subplots(n_rows, n_cols, figsize=(4*n_cols, 4*n_rows))
    if n_genes == 1:
        axes = [axes]
    elif n_rows == 1:
        axes = axes.flatten() if n_genes > 1 else [axes]
    else:
        axes = axes.flatten()
    
    for i, gene_name in enumerate(gene_names):
        ax = axes[i]
        
        gene_expression = expressions[:, i]
        
        if len(phase_hours) == 0:
            ax.text(0.5, 0.5, 'No Data', transform=ax.transAxes, ha='center', va='center')
            ax.set_title(f'{gene_name}', fontsize=10)
            continue
        
        ax.scatter(phase_hours, gene_expression, alpha=0.6, s=20, color='blue', label='Raw Data')
        
        if len(phase_hours) > 5:
            try:
                def sine_func(x, amplitude, phase_shift, offset):
                    return amplitude * np.sin(2 * np.pi * x / 24 + phase_shift) + offset
                
                from scipy.optimize import curve_fit
                popt, _ = curve_fit(sine_func, phase_hours, gene_expression, maxfev=2000)
                
                x_fit = np.linspace(0, 24, 100)
                y_fit = sine_func(x_fit, *popt)
                ax.plot(x_fit, y_fit, '--', color='green', alpha=0.7, linewidth=2, label='Sine Fit')
                
                amplitude, phase_shift, _ = popt
                peak_time = (-phase_shift * 24 / (2 * np.pi)) % 24
                
                y_pred = sine_func(phase_hours, *popt)
                ss_res = np.sum((gene_expression - y_pred) ** 2)
                ss_tot = np.sum((gene_expression - np.mean(gene_expression)) ** 2)
                r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0
                
                ax.text(0.02, 0.98, 
                       f'Peak: {peak_time:.1f}h\nAmp: {amplitude:.3f}\nR²: {r_squared:.3f}', 
                       transform=ax.transAxes, verticalalignment='top', fontsize=8,
                       bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.8))
                
            except Exception as e:
                print(f"拟合失败 {gene_name}: {e}")
        
        ax.set_title(f'{gene_name}', fontsize=10)
        ax.set_xlabel('Predicted Phase (Hours)')
        ax.set_ylabel('Expression Level')
        ax.grid(True, alpha=0.3)
        ax.set_xlim(0, 24)
        ax.set_xticks([0, 6, 12, 18, 24])
        
        if i == 0:
            ax.legend(fontsize=8)
    
    for i in range(n_genes, len(axes)):
        axes[i].set_visible(False)
    
    plt.suptitle(f'Gene Expression vs Predicted Phase - {celltype} (Raw Data)', fontsize=14)
    plt.tight_layout()
    
    filename = f'gene_expression_phase_{celltype}.png'
    filepath = os.path.join(save_dir, filename)
    plt.savefig(filepath, dpi=300, bbox_inches='tight')
    plt.close()
    
    print(f"基因表达相位图（原始数据）已保存: {filepath}")

def plot_celltype_comparison_raw(
    expressions, 
    phase_hours, 
    celltypes, 
    gene_names, 
    valid_celltypes, 
    save_dir,
    n_genes_to_compare=4
):
    print("绘制细胞类型对比图（原始数据）...")
    print(f"有效细胞类型: {valid_celltypes}")
    print(f"选择的基因: {gene_names[:n_genes_to_compare]}")
    
    n_genes_to_compare = min(n_genes_to_compare, len(gene_names))
    top_genes = gene_names[:n_genes_to_compare]
    
    n_cols = min(2, n_genes_to_compare)
    n_rows = (n_genes_to_compare + n_cols - 1) // n_cols
    
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(8*n_cols, 6*n_rows))
    if n_genes_to_compare == 1:
        axes = [axes]
    elif n_rows == 1:
        axes = axes.flatten() if n_genes_to_compare > 1 else [axes]
    else:
        axes = axes.flatten()
    
    colors = plt.cm.tab10(np.linspace(0, 1, len(valid_celltypes)))
    
    for i, gene_name in enumerate(top_genes):
        if i >= len(axes):
            break
            
        ax = axes[i]
        
        for celltype_idx, celltype in enumerate(valid_celltypes):
            celltype_mask = celltypes == celltype
            celltype_expressions = expressions[celltype_mask, i]
            celltype_phases = phase_hours[celltype_mask]
            
            if len(celltype_expressions) < 3:
                continue
            
            ax.scatter(celltype_phases, celltype_expressions, 
                      color=colors[celltype_idx], alpha=0.6, s=15, 
                      label=f'{celltype} (n={len(celltype_expressions)})')
            
            if len(celltype_expressions) > 5:
                try:
                    sorted_indices = np.argsort(celltype_phases)
                    sorted_phases = celltype_phases[sorted_indices]
                    sorted_expressions = celltype_expressions[sorted_indices]
                    
                    window_size = max(3, len(sorted_phases) // 8)
                    if len(sorted_phases) >= window_size:
                        from scipy.ndimage import uniform_filter1d
                        smooth_expression = uniform_filter1d(sorted_expressions.astype(float), size=window_size)
                        ax.plot(sorted_phases, smooth_expression, 
                               color=colors[celltype_idx], linewidth=2, alpha=0.8)
                        
                except Exception as e:
                    print(f"平滑处理失败 {gene_name} - {celltype}: {e}")
        
        ax.set_title(f'{gene_name}', fontsize=14, fontweight='bold')
        ax.set_xlabel('Predicted Phase (Hours)', fontsize=12)
        ax.set_ylabel('Expression Level', fontsize=12)
        ax.grid(True, alpha=0.3)
        ax.set_xlim(0, 24)
        ax.set_xticks([0, 6, 12, 18, 24])
        
        handles, labels = ax.get_legend_handles_labels()
        if len(handles) <= 8:
            ax.legend(fontsize=10, loc='best')
        else:
            ax.legend(handles[:8], labels[:8], fontsize=10, loc='best', 
                     title=f"Showing first 8/{len(handles)} cell types")
    
    for i in range(n_genes_to_compare, len(axes)):
        axes[i].set_visible(False)
    
    plt.suptitle('Gene Expression Comparison Across Cell Types (Raw Data)', 
                 fontsize=16, fontweight='bold')
    plt.tight_layout()
    
    filename = 'gene_expression_celltype_comparison.png'
    filepath = os.path.join(save_dir, filename)
    plt.savefig(filepath, dpi=300, bbox_inches='tight')
    plt.close()
    
    print(f"细胞类型对比图（原始数据）已保存: {filepath}")

def plot_gene_expression_with_custom_data(
    model, 
    test_loader, 
    preprocessing_info, 
    custom_gene_expressions, 
    custom_gene_names, 
    device='cuda', 
    save_dir='./results'
):
    print("\n=== 使用自定义基因数据绘制基因表达相位图（原始数据）===")
    model.eval()
    
    all_phase_hours = []
    all_celltypes = []
    
    with torch.no_grad():
        for batch in test_loader:
            expressions = batch['expression'].to(device)
            celltypes = batch.get('celltype', None)
            
            phase_coords, _ = model(expressions)
            phases = coords_to_phase(phase_coords)
            phase_hours = phases.cpu().numpy() * preprocessing_info.get('period_hours', 24.0) / (2 * np.pi)
            
            all_phase_hours.append(phase_hours)
            
            if celltypes is not None:
                all_celltypes.extend(celltypes)
    
    phase_hours_data = np.concatenate(all_phase_hours)
    
    if all_celltypes:
        celltypes_data = np.array(all_celltypes)
        unique_celltypes = np.unique(celltypes_data)
        print(f"发现细胞类型: {unique_celltypes}")
    else:
        celltypes_data = None
        unique_celltypes = ['All_Samples']
    
    print(f"自定义基因: {custom_gene_names}")
    print(f"基因表达数据维度: {custom_gene_expressions.shape}")
    
    os.makedirs(save_dir, exist_ok=True)
    
    if celltypes_data is not None:
        for celltype in unique_celltypes:
            if celltype == 'PADDING':
                continue
                
            celltype_mask = celltypes_data == celltype
            celltype_expressions = custom_gene_expressions[celltype_mask]
            celltype_phases = phase_hours_data[celltype_mask]
            
            if len(celltype_expressions) < 5:
                continue
            
            plot_celltype_gene_expression_raw(
                celltype_expressions, celltype_phases, custom_gene_names,
                celltype, save_dir
            )
    else:
        plot_celltype_gene_expression_raw(
            custom_gene_expressions, phase_hours_data, custom_gene_names,
            'All_Samples', save_dir
        )
    
    if celltypes_data is not None and len(unique_celltypes) > 1:
        valid_celltypes = [ct for ct in unique_celltypes if ct != 'PADDING']
        if len(valid_celltypes) > 1:
            plot_celltype_comparison_raw(
                custom_gene_expressions, phase_hours_data, celltypes_data, custom_gene_names,
                valid_celltypes, save_dir
            )
    
    print("自定义基因表达相位图绘制完成！")

def main():
    parser = argparse.ArgumentParser(description="训练相位自编码器模型")
    parser.add_argument("--train_file", required=True, help="训练数据文件路径")
    parser.add_argument("--test_file", default=None, help="测试数据文件路径（可选）")
    parser.add_argument("--n_components", type=int, default=50, help="选择的重要基因数量")
    parser.add_argument("--num_epochs", type=int, default=100, help="训练轮数")
    parser.add_argument("--lr", type=float, default=0.001, help="学习率")
    parser.add_argument("--lambda_recon", type=float, default=1.0, help="重建损失权重")
    parser.add_argument("--lambda_time", type=float, default=0.5, help="时间监督损失权重")
    parser.add_argument("--lambda_sine", type=float, default=0.5, help="正弦先验损失权重")
    parser.add_argument("--period_hours", type=float, default=24.0, help="预期周期（小时）")
    parser.add_argument("--dropout", type=float, default=0.2, help="Dropout比例")
    parser.add_argument("--device", default='cuda', help="设备 (cuda/cpu)")
    parser.add_argument("--save_dir", default='./phase_autoencoder_results', help="保存目录")
    parser.add_argument("--random_seed", type=int, default=42, help="随机种子")
    parser.add_argument("--n_genes_plot", type=int, default=10, help="绘制的基因数量（当未指定custom_genes时使用）")
    parser.add_argument("--custom_genes", nargs='*', default=None, required=True, help="指定要绘制的基因列表，例如: --custom_genes GENE1 GENE2 GENE3")
    parser.add_argument("--sine_predictor_hidden", type=int, default=64, help="正弦参数预测器的隐藏层维度")

    args = parser.parse_args()
    
    torch.manual_seed(args.random_seed)
    np.random.seed(args.random_seed)
    
    if args.device == 'cuda' and not torch.cuda.is_available():
        print("CUDA不可用，使用CPU")
        args.device = 'cpu'
    
    print("=== 相位自编码器训练 ===")
    print(f"训练数据文件: {args.train_file}")
    if args.test_file:
        print(f"测试数据文件: {args.test_file}")
    print(f"选择重要基因数: {args.n_components}")

    df_tmp = pd.read_csv(args.train_file, low_memory=False)
    sample_columns_tmp = [col for col in df_tmp.columns if col != 'Gene_Symbol']
    max_samples = len(sample_columns_tmp)
    print(f"自动设置最大样本数量为训练数据样本数: {max_samples}")

    print(f"设备: {args.device}")
    
    if args.custom_genes:
        print(f"用户指定基因: {args.custom_genes}")
    else:
        print(f"将绘制前 {args.n_genes_plot} 个重要基因")
    
    train_dataset, preprocessing_info = load_and_preprocess_train_data(
        args.train_file, args.n_components, max_samples, args.random_seed
    )
    
    preprocessing_info['period_hours'] = args.period_hours
    
    model = PhaseAutoEncoder(
        input_dim=args.n_components,
        dropout=args.dropout
    )
    
    print(f"模型参数数量: {sum(p.numel() for p in model.parameters())}")

    print("创建正弦参数预测器...")
    sine_predictor = SineParameterPredictor(
        max_samples,
        hidden_dim=args.sine_predictor_hidden
    )
    print(f"正弦参数预测器参数数量: {sum(p.numel() for p in sine_predictor.parameters())}")

    print("使用神经网络正弦损失进行端到端训练...")
    train_losses = train_model(
        model=model,
        sine_predictor=sine_predictor,
        train_dataset=train_dataset,
        preprocessing_info=preprocessing_info,
        num_epochs=args.num_epochs,
        lr=args.lr,
        device=args.device,
        lambda_recon=args.lambda_recon,
        lambda_time=args.lambda_time,
        lambda_neural_sine=args.lambda_sine,
        period_hours=args.period_hours,
        save_dir=args.save_dir
    )
    
    plt.figure(figsize=(10, 6))
    plt.plot(train_losses, label='Train Loss')
    plt.xlabel('Epoch')
    plt.ylabel('Loss')
    plt.title('Training Progress')
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.savefig(os.path.join(args.save_dir, 'training_curves.png'), dpi=300, bbox_inches='tight')
    plt.close()
    
    print(f"\n=== 训练完成 ===")
    print(f"训练结果保存到: {args.save_dir}")
    
    if args.test_file:
        print(f"\n=== 开始测试阶段 ===")
        
        test_dataset, test_preprocessing_info = load_and_preprocess_test_data(
            args.test_file, preprocessing_info
        )
        
        test_loader = DataLoader(test_dataset, batch_size=1000, shuffle=False)
        
        _ = predict_and_save_phases(
            model=model,
            test_loader=test_loader,
            preprocessing_info=test_preprocessing_info,
            device=args.device,
            save_dir=args.save_dir
        )
        
        print(f"\n=== 绘制基因表达相位图 ===")
        
        custom_gene_expressions, custom_gene_names = get_original_gene_expressions(
            args.test_file, args.custom_genes, test_preprocessing_info,
            None, None
        )
        plot_gene_expression_with_custom_data(
            model=model,
            test_loader=test_loader,
            preprocessing_info=test_preprocessing_info,
            custom_gene_expressions=custom_gene_expressions,
            custom_gene_names=custom_gene_names,
            device=args.device,
            save_dir=args.save_dir
        )
        
        print(f"\n=== 测试完成 ===")
        print(f"主要输出文件:")
        print(f"  - 模型权重: {args.save_dir}/final_model.pth")
        print(f"  - 详细预测: {args.save_dir}/phase_predictions.csv")
        print(f"  - 简化预测: {args.save_dir}/phase_predictions_simple.csv")
        print(f"  - 训练曲线: {args.save_dir}/training_curves.png")
        print(f"  - 预测分析: {args.save_dir}/prediction_analysis.png")
        print(f"  - 基因表达相位图: {args.save_dir}/gene_expression_phase_*.png")
        print(f"  - 细胞类型对比图: {args.save_dir}/gene_expression_celltype_comparison.png")
    else:
        print(f"\n未提供测试文件，只完成训练阶段")
        print(f"主要输出文件:")
        print(f"  - 模型权重: {args.save_dir}/final_model.pth")
        print(f"  - 训练曲线: {args.save_dir}/training_curves.png")

if __name__ == "__main__":
    main()